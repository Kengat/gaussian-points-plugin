#include "PointCloudRenderer.h"

#include <windows.h>

#include <GL/glew.h>
#include <GL/gl.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <algorithm>
#include <cmath>
#include <fstream>
#include <string>
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

struct PointCloudObject {
  std::string id;
  std::vector<float> points;
  float base_half_extents[3] = {1.0f, 1.0f, 1.0f};
  double center_xyz[3] = {0.0, 0.0, 0.0};
  double half_extents_xyz[3] = {1.0, 1.0, 1.0};
  double axes_xyz[9] = {
      1.0, 0.0, 0.0,
      0.0, 1.0, 0.0,
      0.0, 0.0, 1.0};
  bool visible = true;
  int highlight_mode = 0;
};

std::vector<PointCloudObject> g_pointcloud_objects;
constexpr int kHighlightNone = 0;
constexpr int kHighlightHover = 1;
constexpr int kHighlightSelected = 2;

extern "C" EXPORT int SetPointCloudObjectData(const char* object_id, const double* points_in, int count);
extern "C" EXPORT int SetPointCloudObjectTransform(const char* object_id, const double* center_xyz, const double* half_extents_xyz, const double* axes_xyz, int visible);
extern "C" EXPORT int SetPointCloudObjectHighlight(const char* object_id, int highlight_mode);
extern "C" EXPORT int RemovePointCloudObject(const char* object_id);
extern "C" EXPORT void ClearPointCloudObjects();

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

PointCloudObject* FindObject(const char* object_id) {
  if (object_id == nullptr) {
    return nullptr;
  }

  for (PointCloudObject& object : g_pointcloud_objects) {
    if (object.id == object_id) {
      return &object;
    }
  }

  return nullptr;
}

PointCloudObject& UpsertObject(const char* object_id) {
  PointCloudObject* existing = FindObject(object_id);
  if (existing) {
    return *existing;
  }

  PointCloudObject object;
  object.id = object_id ? object_id : "";
  g_pointcloud_objects.push_back(object);
  return g_pointcloud_objects.back();
}

void ResetObjectTransform(PointCloudObject& object) {
  object.visible = true;
  object.highlight_mode = kHighlightNone;
  object.axes_xyz[0] = 1.0; object.axes_xyz[1] = 0.0; object.axes_xyz[2] = 0.0;
  object.axes_xyz[3] = 0.0; object.axes_xyz[4] = 1.0; object.axes_xyz[5] = 0.0;
  object.axes_xyz[6] = 0.0; object.axes_xyz[7] = 0.0; object.axes_xyz[8] = 1.0;
}

void TransformPoint(const PointCloudObject& object, float local_x, float local_y, float local_z, float* out_xyz) {
  const double sx = object.base_half_extents[0] > 1.0e-6f ? (object.half_extents_xyz[0] / object.base_half_extents[0]) : 1.0;
  const double sy = object.base_half_extents[1] > 1.0e-6f ? (object.half_extents_xyz[1] / object.base_half_extents[1]) : 1.0;
  const double sz = object.base_half_extents[2] > 1.0e-6f ? (object.half_extents_xyz[2] / object.base_half_extents[2]) : 1.0;
  const double scaled_x = static_cast<double>(local_x) * sx;
  const double scaled_y = static_cast<double>(local_y) * sy;
  const double scaled_z = static_cast<double>(local_z) * sz;

  out_xyz[0] = static_cast<float>(
      object.center_xyz[0] +
      (object.axes_xyz[0] * scaled_x) +
      (object.axes_xyz[3] * scaled_y) +
      (object.axes_xyz[6] * scaled_z));
  out_xyz[1] = static_cast<float>(
      object.center_xyz[1] +
      (object.axes_xyz[1] * scaled_x) +
      (object.axes_xyz[4] * scaled_y) +
      (object.axes_xyz[7] * scaled_z));
  out_xyz[2] = static_cast<float>(
      object.center_xyz[2] +
      (object.axes_xyz[2] * scaled_x) +
      (object.axes_xyz[5] * scaled_y) +
      (object.axes_xyz[8] * scaled_z));
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

void HighlightColorForMode(int highlight_mode, float* r, float* g, float* b, float* a) {
  if (highlight_mode == kHighlightHover) {
    *r = 1.0f; *g = 0.58f; *b = 0.04f; *a = 1.0f;
  } else {
    *r = 1.0f; *g = 0.84f; *b = 0.08f; *a = 1.0f;
  }
}

float HighlightMaskPointSizeForMode(int highlight_mode) {
  return highlight_mode == kHighlightHover ? 13.0f : 11.0f;
}

float HighlightOutlinePointSizeForMode(int highlight_mode) {
  return highlight_mode == kHighlightHover ? 20.0f : 16.0f;
}

void DrawPointCloudObject(
    const PointCloudObject& object,
    const ClipBoxState& clip_box,
    bool highlight_pass) {
  const GLsizei object_point_count = static_cast<GLsizei>(object.points.size() / 7);
  float r = 0.0f;
  float g = 0.0f;
  float b = 0.0f;
  float a = 1.0f;
  if (highlight_pass) {
    HighlightColorForMode(object.highlight_mode, &r, &g, &b, &a);
  }

  glBegin(GL_POINTS);
  for (GLsizei i = 0; i < object_point_count; ++i) {
    const size_t base = static_cast<size_t>(i) * 7;
    float world[3] = {0.0f, 0.0f, 0.0f};
    TransformPoint(object, object.points[base + 0], object.points[base + 1], object.points[base + 2], world);
    if (!IsPointInsideClip(clip_box, world[0], world[1], world[2])) {
      continue;
    }

    if (highlight_pass) {
      glColor4f(r, g, b, a);
    } else {
      glColor4f(
          object.points[base + 3],
          object.points[base + 4],
          object.points[base + 5],
          object.points[base + 6]);
    }
    glVertex3f(world[0], world[1], world[2]);
  }
  glEnd();
}

}  // namespace

extern "C" EXPORT void SetPointCloud(const double* points_in, int count) {
  ClearPointCloudObjects();
  if (points_in == nullptr || count <= 0) {
    LogMessage("SetPointCloud called with invalid input.");
    return;
  }

  SetPointCloudObjectData("__legacy__", points_in, count);
}

extern "C" EXPORT int SetPointCloudObjectData(const char* object_id, const double* points_in, int count) {
  if (object_id == nullptr || points_in == nullptr || count <= 0) {
    LogMessage("SetPointCloudObjectData received invalid input.");
    return 0;
  }

  PointCloudObject& object = UpsertObject(object_id);
  object.points.clear();
  object.points.reserve(static_cast<size_t>(count) * 7);
  ResetObjectTransform(object);

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

    object.points.push_back(x);
    object.points.push_back(y);
    object.points.push_back(z);
    object.points.push_back(static_cast<float>(points_in[i * 6 + 3]) / 255.0f);
    object.points.push_back(static_cast<float>(points_in[i * 6 + 4]) / 255.0f);
    object.points.push_back(static_cast<float>(points_in[i * 6 + 5]) / 255.0f);
    object.points.push_back(1.0f);
  }

  if (have_bounds) {
    object.base_half_extents[0] = ((max_x - min_x) * 0.5f > 0.001f) ? ((max_x - min_x) * 0.5f) : 0.001f;
    object.base_half_extents[1] = ((max_y - min_y) * 0.5f > 0.001f) ? ((max_y - min_y) * 0.5f) : 0.001f;
    object.base_half_extents[2] = ((max_z - min_z) * 0.5f > 0.001f) ? ((max_z - min_z) * 0.5f) : 0.001f;
    object.half_extents_xyz[0] = object.base_half_extents[0];
    object.half_extents_xyz[1] = object.base_half_extents[1];
    object.half_extents_xyz[2] = object.base_half_extents[2];
  }

  g_data_ready = !g_pointcloud_objects.empty();
  g_gpu_data_dirty = true;
  LogMessage("SetPointCloudObjectData stored %d points for '%s'.", count, object.id.c_str());
  return 1;
}

extern "C" EXPORT int SetPointCloudObjectTransform(
    const char* object_id,
    const double* center_xyz,
    const double* half_extents_xyz,
    const double* axes_xyz,
    int visible) {
  PointCloudObject* object = FindObject(object_id);
  if (object == nullptr) {
    return 0;
  }

  if (center_xyz != nullptr) {
    memcpy(object->center_xyz, center_xyz, sizeof(object->center_xyz));
  }
  if (half_extents_xyz != nullptr) {
    memcpy(object->half_extents_xyz, half_extents_xyz, sizeof(object->half_extents_xyz));
  }
  if (axes_xyz != nullptr) {
    memcpy(object->axes_xyz, axes_xyz, sizeof(object->axes_xyz));
  }
  object->visible = visible != 0;
  g_data_ready = !g_pointcloud_objects.empty();
  return 1;
}

extern "C" EXPORT int SetPointCloudObjectHighlight(const char* object_id, int highlight_mode) {
  PointCloudObject* object = FindObject(object_id);
  if (object == nullptr) {
    return 0;
  }

  object->highlight_mode = highlight_mode;
  return 1;
}

extern "C" EXPORT int RemovePointCloudObject(const char* object_id) {
  if (object_id == nullptr) {
    return 0;
  }

  const auto new_end = std::remove_if(
      g_pointcloud_objects.begin(),
      g_pointcloud_objects.end(),
      [object_id](const PointCloudObject& object) { return object.id == object_id; });
  if (new_end == g_pointcloud_objects.end()) {
    return 0;
  }

  g_pointcloud_objects.erase(new_end, g_pointcloud_objects.end());
  g_data_ready = !g_pointcloud_objects.empty();
  return 1;
}

extern "C" EXPORT void ClearPointCloudObjects() {
  g_pointcloud_objects.clear();
  g_points.clear();
  g_data_ready = false;
  g_gpu_data_dirty = false;
}

extern "C" EXPORT void renderPointCloud() {
  if (!g_data_ready || g_pointcloud_objects.empty()) {
    return;
  }

  if (!LoadHookFunctions() || !InitializeRenderer()) {
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
  GLint stencil_bits = 0;
  GLint old_stencil_func = GL_ALWAYS;
  GLint old_stencil_ref = 0;
  GLint old_stencil_value_mask = ~0;
  GLint old_stencil_write_mask = ~0;
  GLint old_stencil_fail = GL_KEEP;
  GLint old_stencil_zfail = GL_KEEP;
  GLint old_stencil_zpass = GL_KEEP;
  GLboolean depth_enabled = glIsEnabled(GL_DEPTH_TEST);
  GLboolean blend_enabled = glIsEnabled(GL_BLEND);
  GLboolean stencil_enabled = glIsEnabled(GL_STENCIL_TEST);
  GLboolean program_point_size_enabled = glIsEnabled(GL_PROGRAM_POINT_SIZE);
  GLboolean texture_2d_enabled = glIsEnabled(GL_TEXTURE_2D);
  GLboolean depth_mask = GL_TRUE;
  GLboolean color_mask[4] = {GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE};
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
  glGetBooleanv(GL_COLOR_WRITEMASK, color_mask);
  glGetIntegerv(GL_STENCIL_BITS, &stencil_bits);
  glGetIntegerv(GL_DEPTH_FUNC, &old_depth_func);
  glGetIntegerv(GL_BLEND_SRC_RGB, &old_blend_src_rgb);
  glGetIntegerv(GL_BLEND_DST_RGB, &old_blend_dst_rgb);
  glGetIntegerv(GL_BLEND_SRC_ALPHA, &old_blend_src_alpha);
  glGetIntegerv(GL_BLEND_DST_ALPHA, &old_blend_dst_alpha);
  glGetIntegerv(GL_STENCIL_FUNC, &old_stencil_func);
  glGetIntegerv(GL_STENCIL_REF, &old_stencil_ref);
  glGetIntegerv(GL_STENCIL_VALUE_MASK, &old_stencil_value_mask);
  glGetIntegerv(GL_STENCIL_WRITEMASK, &old_stencil_write_mask);
  glGetIntegerv(GL_STENCIL_FAIL, &old_stencil_fail);
  glGetIntegerv(GL_STENCIL_PASS_DEPTH_FAIL, &old_stencil_zfail);
  glGetIntegerv(GL_STENCIL_PASS_DEPTH_PASS, &old_stencil_zpass);
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

  GLsizei point_count = 0;
  for (const PointCloudObject& object : g_pointcloud_objects) {
    if (!object.visible) {
      continue;
    }

    point_count += static_cast<GLsizei>(object.points.size() / 7);
    DrawPointCloudObject(object, clip_box, false);
  }
  LogMessage("Rendering %d points.", point_count);

  bool has_highlighted_objects = false;
  for (const PointCloudObject& object : g_pointcloud_objects) {
    if (object.visible && object.highlight_mode != kHighlightNone) {
      has_highlighted_objects = true;
      break;
    }
  }

  if (has_highlighted_objects && stencil_bits > 0) {
    glEnable(GL_STENCIL_TEST);
    glStencilMask(0x80);
    glClearStencil(0);
    glClear(GL_STENCIL_BUFFER_BIT);
    glStencilFunc(GL_ALWAYS, 0x80, 0x80);
    glStencilOp(GL_KEEP, GL_KEEP, GL_REPLACE);
    glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE);
    glDepthMask(GL_FALSE);

    for (const PointCloudObject& object : g_pointcloud_objects) {
      if (!object.visible || object.highlight_mode == kHighlightNone) {
        continue;
      }

      glPointSize(HighlightMaskPointSizeForMode(object.highlight_mode));
      DrawPointCloudObject(object, clip_box, false);
    }

    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glStencilMask(0x00);
    glStencilFunc(GL_NOTEQUAL, 0x80, 0x80);
    glStencilOp(GL_KEEP, GL_KEEP, GL_KEEP);
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);

    for (const PointCloudObject& object : g_pointcloud_objects) {
      if (!object.visible || object.highlight_mode == kHighlightNone) {
        continue;
      }

      glPointSize(HighlightOutlinePointSizeForMode(object.highlight_mode));
      DrawPointCloudObject(object, clip_box, true);
    }

    glDisable(GL_BLEND);
    glDisable(GL_STENCIL_TEST);
    glDepthMask(GL_TRUE);
  }

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

  if (stencil_enabled) {
    glEnable(GL_STENCIL_TEST);
  } else {
    glDisable(GL_STENCIL_TEST);
  }
  glStencilFunc(old_stencil_func, old_stencil_ref, old_stencil_value_mask);
  glStencilMask(old_stencil_write_mask);
  glStencilOp(old_stencil_fail, old_stencil_zfail, old_stencil_zpass);
  glColorMask(color_mask[0], color_mask[1], color_mask[2], color_mask[3]);
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
