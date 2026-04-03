#include "PointCloudRenderer.h"

#include <windows.h>

#include <GL/glew.h>
#include <GL/gl.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <fstream>
#include <vector>

namespace {

using PFN_GET_MATRICES = bool (*)(float* modelview, float* projection);
using PFN_GET_CLIP_BOX_STATE = bool (*)(int* enabled, double* center_xyz, double* half_extents_xyz, double* axes_xyz);

struct ClipBoxState {
  bool enabled = false;
  double center_xyz[3] = {0.0, 0.0, 0.0};
  double half_extents_xyz[3] = {0.0, 0.0, 0.0};
  double axes_xyz[9] = {
      1.0, 0.0, 0.0,
      0.0, 1.0, 0.0,
      0.0, 0.0, 1.0};
};

PFN_GET_MATRICES g_get_current_matrices = nullptr;
PFN_GET_CLIP_BOX_STATE g_get_clip_box_state = nullptr;
std::vector<float> g_points;
bool g_data_ready = false;
bool g_gpu_data_dirty = false;
GLuint g_program = 0;
GLuint g_vao = 0;
GLuint g_vbo = 0;

void LogMessage(const char* format, ...) {
  char buffer[1024];
  va_list args;
  va_start(args, format);
  vsnprintf(buffer, sizeof(buffer), format, args);
  va_end(args);
  OutputDebugStringA("[PointCloudRenderer] ");
  OutputDebugStringA(buffer);
  OutputDebugStringA("\n");

  char temp_path[MAX_PATH] = {};
  DWORD length = GetTempPathA(MAX_PATH, temp_path);
  if (length == 0 || length > MAX_PATH) {
    return;
  }

  std::ofstream log_file(std::string(temp_path) + "pointcloud_renderer.log", std::ios::app);
  if (log_file.is_open()) {
    log_file << "[PointCloudRenderer] " << buffer << "\n";
  }
}

GLuint CompileShader(GLenum type, const char* source) {
  GLuint shader = glCreateShader(type);
  if (shader == 0) {
    LogMessage("glCreateShader failed.");
    return 0;
  }

  glShaderSource(shader, 1, &source, nullptr);
  glCompileShader(shader);

  GLint success = GL_FALSE;
  glGetShaderiv(shader, GL_COMPILE_STATUS, &success);
  if (success != GL_TRUE) {
    char info_log[512];
    glGetShaderInfoLog(shader, sizeof(info_log), nullptr, info_log);
    LogMessage("Shader compilation failed: %s", info_log);
    glDeleteShader(shader);
    return 0;
  }

  return shader;
}

void MultiplyMat4(const float* left, const float* right, float* result) {
  for (int row = 0; row < 4; ++row) {
    for (int col = 0; col < 4; ++col) {
      result[row * 4 + col] =
          left[row * 4 + 0] * right[0 * 4 + col] +
          left[row * 4 + 1] * right[1 * 4 + col] +
          left[row * 4 + 2] * right[2 * 4 + col] +
          left[row * 4 + 3] * right[3 * 4 + col];
    }
  }
}

void TransposeMat4(const float* input, float* output) {
  for (int row = 0; row < 4; ++row) {
    for (int col = 0; col < 4; ++col) {
      output[col * 4 + row] = input[row * 4 + col];
    }
  }
}

bool LoadHookFunctions() {
  if (g_get_current_matrices != nullptr && g_get_clip_box_state != nullptr) {
    return true;
  }

  HMODULE hook_dll = GetModuleHandleA("SketchUpOverlayBridge.dll");
  if (hook_dll == nullptr) {
    LogMessage("SketchUpOverlayBridge.dll is not loaded.");
    return false;
  }

  g_get_current_matrices = reinterpret_cast<PFN_GET_MATRICES>(
      GetProcAddress(hook_dll, "GetCurrentMatrices"));
  if (g_get_current_matrices == nullptr) {
    LogMessage("GetCurrentMatrices export not found.");
    return false;
  }

  g_get_clip_box_state = reinterpret_cast<PFN_GET_CLIP_BOX_STATE>(
      GetProcAddress(hook_dll, "GetClipBoxState"));
  if (g_get_clip_box_state == nullptr) {
    LogMessage("GetClipBoxState export not found.");
  }

  return true;
}

ClipBoxState FetchClipBoxState() {
  ClipBoxState state;
  if (g_get_clip_box_state == nullptr) {
    return state;
  }

  int enabled = 0;
  if (!g_get_clip_box_state(&enabled, state.center_xyz, state.half_extents_xyz, state.axes_xyz)) {
    return state;
  }

  state.enabled = enabled != 0;
  return state;
}

bool IsPointInsideClip(const ClipBoxState& clip_box, float x, float y, float z) {
  if (!clip_box.enabled) {
    return true;
  }

  const double local_x =
      (x - clip_box.center_xyz[0]) * clip_box.axes_xyz[0] +
      (y - clip_box.center_xyz[1]) * clip_box.axes_xyz[1] +
      (z - clip_box.center_xyz[2]) * clip_box.axes_xyz[2];
  const double local_y =
      (x - clip_box.center_xyz[0]) * clip_box.axes_xyz[3] +
      (y - clip_box.center_xyz[1]) * clip_box.axes_xyz[4] +
      (z - clip_box.center_xyz[2]) * clip_box.axes_xyz[5];
  const double local_z =
      (x - clip_box.center_xyz[0]) * clip_box.axes_xyz[6] +
      (y - clip_box.center_xyz[1]) * clip_box.axes_xyz[7] +
      (z - clip_box.center_xyz[2]) * clip_box.axes_xyz[8];

  return std::fabs(local_x) <= clip_box.half_extents_xyz[0] &&
      std::fabs(local_y) <= clip_box.half_extents_xyz[1] &&
      std::fabs(local_z) <= clip_box.half_extents_xyz[2];
}

bool InitializeRenderer() {
  if (g_program != 0) {
    return true;
  }

  GLenum err = glewInit();
  if (err != GLEW_OK) {
    LogMessage("GLEW init failed: %s", glewGetErrorString(err));
    return false;
  }

  const char* vertex_shader_source = R"(
    #version 150
    in vec3 aPosition;
    in vec4 aColor;
    uniform mat4 uMVP;
    uniform float uPointSize;
    out vec4 vColor;
    void main() {
      gl_Position = uMVP * vec4(aPosition, 1.0);
      gl_PointSize = uPointSize;
      vColor = aColor;
    }
  )";

  const char* fragment_shader_source = R"(
    #version 150
    in vec4 vColor;
    out vec4 fragColor;
    void main() {
      vec2 coord = gl_PointCoord * 2.0 - 1.0;
      if (dot(coord, coord) > 1.0) {
        discard;
      }
      fragColor = vColor;
    }
  )";

  GLuint vertex_shader = CompileShader(GL_VERTEX_SHADER, vertex_shader_source);
  GLuint fragment_shader = CompileShader(GL_FRAGMENT_SHADER, fragment_shader_source);
  if (vertex_shader == 0 || fragment_shader == 0) {
    if (vertex_shader != 0) glDeleteShader(vertex_shader);
    if (fragment_shader != 0) glDeleteShader(fragment_shader);
    return false;
  }

  g_program = glCreateProgram();
  glAttachShader(g_program, vertex_shader);
  glAttachShader(g_program, fragment_shader);
  glBindAttribLocation(g_program, 0, "aPosition");
  glBindAttribLocation(g_program, 1, "aColor");
  glLinkProgram(g_program);

  GLint success = GL_FALSE;
  glGetProgramiv(g_program, GL_LINK_STATUS, &success);
  glDeleteShader(vertex_shader);
  glDeleteShader(fragment_shader);

  if (success != GL_TRUE) {
    char info_log[512];
    glGetProgramInfoLog(g_program, sizeof(info_log), nullptr, info_log);
    LogMessage("Program link failed: %s", info_log);
    glDeleteProgram(g_program);
    g_program = 0;
    return false;
  }

  glGenVertexArrays(1, &g_vao);
  glGenBuffers(1, &g_vbo);
  return g_program != 0 && g_vao != 0 && g_vbo != 0;
}

void CleanupRenderer() {
  if (g_vbo != 0) glDeleteBuffers(1, &g_vbo);
  if (g_vao != 0) glDeleteVertexArrays(1, &g_vao);
  if (g_program != 0) glDeleteProgram(g_program);
  g_vbo = 0;
  g_vao = 0;
  g_program = 0;
}

void UploadPointDataIfNeeded() {
  if (!g_gpu_data_dirty) {
    return;
  }

  if (!InitializeRenderer()) {
    return;
  }

  glBindVertexArray(g_vao);
  glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
  glBufferData(GL_ARRAY_BUFFER,
      static_cast<GLsizeiptr>(g_points.size() * sizeof(float)),
      g_points.data(),
      GL_STATIC_DRAW);

  glEnableVertexAttribArray(0);
  glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 7 * sizeof(float), reinterpret_cast<void*>(0));
  glEnableVertexAttribArray(1);
  glVertexAttribPointer(1, 4, GL_FLOAT, GL_FALSE, 7 * sizeof(float), reinterpret_cast<void*>(3 * sizeof(float)));

  glBindBuffer(GL_ARRAY_BUFFER, 0);
  glBindVertexArray(0);
  g_gpu_data_dirty = false;
}

}  // namespace

extern "C" EXPORT void SetPointCloud(const double* points_in, int count) {
  if (points_in == nullptr || count <= 0) {
    LogMessage("SetPointCloud called with invalid input.");
    g_points.clear();
    g_data_ready = false;
    g_gpu_data_dirty = false;
    return;
  }

  LogMessage("SetPointCloud received %d points.", count);
  g_points.clear();
  g_points.reserve(static_cast<size_t>(count) * 7);

  float min_x = 0.0f;
  float min_y = 0.0f;
  float min_z = 0.0f;
  float max_x = 0.0f;
  float max_y = 0.0f;
  float max_z = 0.0f;
  bool have_bounds = false;

  for (int i = 0; i < count; ++i) {
    const float x = static_cast<float>(points_in[i * 6 + 0]);
    const float y = static_cast<float>(points_in[i * 6 + 1]);
    const float z = static_cast<float>(points_in[i * 6 + 2]);

    if (!have_bounds) {
      min_x = max_x = x;
      min_y = max_y = y;
      min_z = max_z = z;
      have_bounds = true;
    } else {
      if (x < min_x) min_x = x;
      if (y < min_y) min_y = y;
      if (z < min_z) min_z = z;
      if (x > max_x) max_x = x;
      if (y > max_y) max_y = y;
      if (z > max_z) max_z = z;
    }

    g_points.push_back(x);
    g_points.push_back(y);
    g_points.push_back(z);
    g_points.push_back(static_cast<float>(points_in[i * 6 + 3]) / 255.0f);
    g_points.push_back(static_cast<float>(points_in[i * 6 + 4]) / 255.0f);
    g_points.push_back(static_cast<float>(points_in[i * 6 + 5]) / 255.0f);
    g_points.push_back(1.0f);
  }

  if (have_bounds) {
    LogMessage(
        "Bounds min=(%.3f, %.3f, %.3f) max=(%.3f, %.3f, %.3f)",
        min_x, min_y, min_z, max_x, max_y, max_z);
  }

  g_data_ready = true;
  g_gpu_data_dirty = true;
}

extern "C" EXPORT void renderPointCloud() {
  if (!g_data_ready || g_points.empty()) {
    return;
  }

  if (!LoadHookFunctions() || !InitializeRenderer()) {
    return;
  }

  UploadPointDataIfNeeded();
  if (g_gpu_data_dirty) {
    LogMessage("GPU upload is still pending. Skipping frame.");
    return;
  }

  float view[16] = {0.0f};
  float projection[16] = {0.0f};
  if (!g_get_current_matrices(view, projection)) {
    LogMessage("GetCurrentMatrices failed.");
    return;
  }
  const ClipBoxState clip_box = FetchClipBoxState();

  float mvp[16] = {0.0f};
  MultiplyMat4(projection, view, mvp);
  float projection_gl[16] = {0.0f};
  float view_gl[16] = {0.0f};
  TransposeMat4(projection, projection_gl);
  TransposeMat4(view, view_gl);

  GLint old_program = 0;
  GLint old_vao = 0;
  GLint old_array_buffer = 0;
  GLint old_matrix_mode = 0;
  GLboolean depth_enabled = glIsEnabled(GL_DEPTH_TEST);
  GLboolean blend_enabled = glIsEnabled(GL_BLEND);
  GLboolean program_point_size_enabled = glIsEnabled(GL_PROGRAM_POINT_SIZE);
  GLboolean texture_2d_enabled = glIsEnabled(GL_TEXTURE_2D);
  GLboolean depth_mask = GL_TRUE;
  GLint old_depth_func = GL_LESS;
  GLint old_blend_src_rgb = GL_ONE;
  GLint old_blend_dst_rgb = GL_ZERO;
  GLint old_blend_src_alpha = GL_ONE;
  GLint old_blend_dst_alpha = GL_ZERO;
  GLfloat old_point_size = 1.0f;

  glGetIntegerv(GL_CURRENT_PROGRAM, &old_program);
  glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &old_vao);
  glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &old_array_buffer);
  glGetIntegerv(GL_MATRIX_MODE, &old_matrix_mode);
  glGetBooleanv(GL_DEPTH_WRITEMASK, &depth_mask);
  glGetIntegerv(GL_DEPTH_FUNC, &old_depth_func);
  glGetIntegerv(GL_BLEND_SRC_RGB, &old_blend_src_rgb);
  glGetIntegerv(GL_BLEND_DST_RGB, &old_blend_dst_rgb);
  glGetIntegerv(GL_BLEND_SRC_ALPHA, &old_blend_src_alpha);
  glGetIntegerv(GL_BLEND_DST_ALPHA, &old_blend_dst_alpha);
  glGetFloatv(GL_POINT_SIZE, &old_point_size);

  glEnable(GL_DEPTH_TEST);
  glDepthFunc(GL_LEQUAL);
  glDepthMask(GL_TRUE);
  glDisable(GL_BLEND);
  glDisable(GL_PROGRAM_POINT_SIZE);
  glDisable(GL_TEXTURE_2D);

  glUseProgram(0);
  glBindVertexArray(0);
  glBindBuffer(GL_ARRAY_BUFFER, 0);
  glMatrixMode(GL_PROJECTION);
  glPushMatrix();
  glLoadMatrixf(projection_gl);
  glMatrixMode(GL_MODELVIEW);
  glPushMatrix();
  glLoadMatrixf(view_gl);
  glPointSize(4.0f);

  const GLsizei point_count = static_cast<GLsizei>(g_points.size() / 7);
  GLsizei visible_count = 0;
  glBegin(GL_POINTS);
  for (GLsizei i = 0; i < point_count; ++i) {
    const size_t base = static_cast<size_t>(i) * 7;
    if (!IsPointInsideClip(
            clip_box,
            g_points[base + 0],
            g_points[base + 1],
            g_points[base + 2])) {
      continue;
    }
    ++visible_count;
    glColor4f(
        g_points[base + 3],
        g_points[base + 4],
        g_points[base + 5],
        g_points[base + 6]);
    glVertex3f(
        g_points[base + 0],
        g_points[base + 1],
        g_points[base + 2]);
  }
  glEnd();
  LogMessage("Rendering %d visible points (of %d).", visible_count, point_count);

  GLenum err = glGetError();
  if (err != GL_NO_ERROR) {
    LogMessage("OpenGL error after glDrawArrays: 0x%x", err);
  }

  glPointSize(old_point_size);
  glMatrixMode(GL_MODELVIEW);
  glPopMatrix();
  glMatrixMode(GL_PROJECTION);
  glPopMatrix();
  glMatrixMode(old_matrix_mode);
  glBindVertexArray(old_vao);
  glBindBuffer(GL_ARRAY_BUFFER, old_array_buffer);
  glUseProgram(old_program);

  if (depth_enabled) {
    glEnable(GL_DEPTH_TEST);
    glDepthFunc(old_depth_func);
  } else {
    glDisable(GL_DEPTH_TEST);
  }

  if (blend_enabled) {
    glEnable(GL_BLEND);
    glBlendFuncSeparate(old_blend_src_rgb, old_blend_dst_rgb, old_blend_src_alpha, old_blend_dst_alpha);
  } else {
    glDisable(GL_BLEND);
  }

  glDepthMask(depth_mask);

  if (program_point_size_enabled) {
    glEnable(GL_PROGRAM_POINT_SIZE);
  } else {
    glDisable(GL_PROGRAM_POINT_SIZE);
  }

  if (texture_2d_enabled) {
    glEnable(GL_TEXTURE_2D);
  } else {
    glDisable(GL_TEXTURE_2D);
  }
}

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_DETACH) {
    CleanupRenderer();
  }
  return TRUE;
}
