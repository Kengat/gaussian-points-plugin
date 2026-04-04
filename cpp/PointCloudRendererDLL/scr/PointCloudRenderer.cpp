#include "PointCloudRenderer.h"

#define NOMINMAX
#include <windows.h>

#include <GL/glew.h>
#include <GL/gl.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <limits>
#include <string>
#include <vector>

#undef min
#undef max

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
bool g_data_ready = false;
GLuint g_program = 0;
GLint g_u_view_projection = -1;
GLint g_u_object = -1;
GLint g_u_point_size = -1;
GLint g_u_use_override_color = -1;
GLint g_u_override_color = -1;
GLint g_u_clip_enabled = -1;
GLint g_u_clip_center = -1;
GLint g_u_clip_half_extents = -1;
GLint g_u_clip_axis_x = -1;
GLint g_u_clip_axis_y = -1;
GLint g_u_clip_axis_z = -1;

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
  GLuint vao = 0;
  GLuint vbo = 0;
  bool gpu_dirty = true;
  GLsizei gpu_point_count = 0;
};

std::vector<PointCloudObject> g_pointcloud_objects;
constexpr int kHighlightNone = 0;
constexpr int kHighlightHover = 1;
constexpr int kHighlightSelected = 2;
constexpr GLsizei kHeavyHighlightPointThreshold = 1000000;
constexpr char kGaspMagic[4] = {'G', 'A', 'S', 'P'};
constexpr std::uint32_t kGaspVersion = 2u;
constexpr std::uint32_t kGaspPointStride = 7u;

extern "C" EXPORT int SetPointCloudObjectData(const char* object_id, const double* points_in, int count);
extern "C" EXPORT int LoadPointCloudObjectFromGasp(const char* object_id, const char* filename, double* out_center_xyz, double* out_half_extents_xyz);
extern "C" EXPORT int SetPointCloudObjectTransform(const char* object_id, const double* center_xyz, const double* half_extents_xyz, const double* axes_xyz, int visible);
extern "C" EXPORT int SetPointCloudObjectHighlight(const char* object_id, int highlight_mode);
extern "C" EXPORT int RemovePointCloudObject(const char* object_id);
extern "C" EXPORT void ClearPointCloudObjects();

struct GaspHeader {
  char magic[4];
  std::uint32_t version = 0;
  std::uint32_t flags = 0;
  std::uint64_t full_count = 0;
  std::uint64_t preview_count = 0;
  std::uint32_t full_stride = 0;
  std::uint32_t preview_stride = 0;
  std::uint32_t name_length = 0;
  std::uint32_t source_length = 0;
  double center_xyz[3] = {0.0, 0.0, 0.0};
  double half_extents_xyz[3] = {0.0, 0.0, 0.0};
};

std::wstring Utf8ToWide(const char* utf8_text) {
  if (utf8_text == nullptr || utf8_text[0] == '\0') {
    return std::wstring();
  }

  const int wide_length = MultiByteToWideChar(CP_UTF8, 0, utf8_text, -1, nullptr, 0);
  if (wide_length <= 1) {
    return std::wstring();
  }

  std::wstring wide_text(static_cast<size_t>(wide_length), L'\0');
  if (MultiByteToWideChar(CP_UTF8, 0, utf8_text, -1, &wide_text[0], wide_length) <= 0) {
    return std::wstring();
  }
  wide_text.resize(static_cast<size_t>(wide_length - 1));

  return wide_text;
}

bool ReadExact(std::ifstream& input, void* destination, size_t byte_count) {
  if (byte_count == 0) {
    return true;
  }

  input.read(reinterpret_cast<char*>(destination), static_cast<std::streamsize>(byte_count));
  return input.good();
}

bool SkipBytes(std::ifstream& input, std::uint64_t byte_count) {
  if (byte_count == 0) {
    return true;
  }
  if (byte_count > static_cast<std::uint64_t>((std::numeric_limits<std::streamoff>::max)())) {
    return false;
  }

  input.seekg(static_cast<std::streamoff>(byte_count), std::ios::cur);
  return input.good();
}

bool ReadGaspHeader(std::ifstream& input, GaspHeader* header) {
  if (header == nullptr) {
    return false;
  }

  return
      ReadExact(input, header->magic, sizeof(header->magic)) &&
      ReadExact(input, &header->version, sizeof(header->version)) &&
      ReadExact(input, &header->flags, sizeof(header->flags)) &&
      ReadExact(input, &header->full_count, sizeof(header->full_count)) &&
      ReadExact(input, &header->preview_count, sizeof(header->preview_count)) &&
      ReadExact(input, &header->full_stride, sizeof(header->full_stride)) &&
      ReadExact(input, &header->preview_stride, sizeof(header->preview_stride)) &&
      ReadExact(input, &header->name_length, sizeof(header->name_length)) &&
      ReadExact(input, &header->source_length, sizeof(header->source_length)) &&
      ReadExact(input, header->center_xyz, sizeof(header->center_xyz)) &&
      ReadExact(input, header->half_extents_xyz, sizeof(header->half_extents_xyz));
}

void NormalizeLoadedPointColorsIfNeeded(PointCloudObject& object) {
  bool needs_normalization = false;
  for (size_t index = 0; index + 6 < object.points.size(); index += 7) {
    if (object.points[index + 3] > 1.0f ||
        object.points[index + 4] > 1.0f ||
        object.points[index + 5] > 1.0f ||
        object.points[index + 6] > 1.0f) {
      needs_normalization = true;
      break;
    }
  }

  if (!needs_normalization) {
    return;
  }

  for (size_t index = 0; index + 6 < object.points.size(); index += 7) {
    object.points[index + 3] /= 255.0f;
    object.points[index + 4] /= 255.0f;
    object.points[index + 5] /= 255.0f;
    object.points[index + 6] /= 255.0f;
  }
}

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

void ReleaseObjectGPUResources(PointCloudObject& object) {
  if (object.vbo != 0) {
    glDeleteBuffers(1, &object.vbo);
    object.vbo = 0;
  }
  if (object.vao != 0) {
    glDeleteVertexArrays(1, &object.vao);
    object.vao = 0;
  }
  object.gpu_point_count = 0;
  object.gpu_dirty = true;
}

void BuildObjectMatrix(const PointCloudObject& object, float* out_matrix) {
  const double sx = object.base_half_extents[0] > 1.0e-6f ? (object.half_extents_xyz[0] / object.base_half_extents[0]) : 1.0;
  const double sy = object.base_half_extents[1] > 1.0e-6f ? (object.half_extents_xyz[1] / object.base_half_extents[1]) : 1.0;
  const double sz = object.base_half_extents[2] > 1.0e-6f ? (object.half_extents_xyz[2] / object.base_half_extents[2]) : 1.0;
  out_matrix[0] = static_cast<float>(object.axes_xyz[0] * sx);
  out_matrix[1] = static_cast<float>(object.axes_xyz[3] * sy);
  out_matrix[2] = static_cast<float>(object.axes_xyz[6] * sz);
  out_matrix[3] = static_cast<float>(object.center_xyz[0]);
  out_matrix[4] = static_cast<float>(object.axes_xyz[1] * sx);
  out_matrix[5] = static_cast<float>(object.axes_xyz[4] * sy);
  out_matrix[6] = static_cast<float>(object.axes_xyz[7] * sz);
  out_matrix[7] = static_cast<float>(object.center_xyz[1]);
  out_matrix[8] = static_cast<float>(object.axes_xyz[2] * sx);
  out_matrix[9] = static_cast<float>(object.axes_xyz[5] * sy);
  out_matrix[10] = static_cast<float>(object.axes_xyz[8] * sz);
  out_matrix[11] = static_cast<float>(object.center_xyz[2]);
  out_matrix[12] = 0.0f;
  out_matrix[13] = 0.0f;
  out_matrix[14] = 0.0f;
  out_matrix[15] = 1.0f;
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
    uniform mat4 uViewProjection;
    uniform mat4 uObject;
    uniform float uPointSize;
    uniform int uUseOverrideColor;
    uniform vec4 uOverrideColor;
    uniform int uClipEnabled;
    uniform vec3 uClipCenter;
    uniform vec3 uClipHalfExtents;
    uniform vec3 uClipAxisX;
    uniform vec3 uClipAxisY;
    uniform vec3 uClipAxisZ;
    out vec4 vColor;
    flat out int vVisible;
    void main() {
      vec4 world = uObject * vec4(aPosition, 1.0);
      bool visible = true;
      if (uClipEnabled != 0) {
        vec3 delta = world.xyz - uClipCenter;
        vec3 local = vec3(
          dot(delta, uClipAxisX),
          dot(delta, uClipAxisY),
          dot(delta, uClipAxisZ)
        );
        visible = all(lessThanEqual(abs(local), uClipHalfExtents));
      }
      vVisible = visible ? 1 : 0;
      gl_Position = visible ? (uViewProjection * world) : vec4(2.0, 2.0, 2.0, 1.0);
      gl_PointSize = uPointSize;
      vColor = (uUseOverrideColor != 0) ? uOverrideColor : aColor;
    }
  )";

  const char* fragment_shader_source = R"(
    #version 150
    in vec4 vColor;
    flat in int vVisible;
    out vec4 fragColor;
    void main() {
      if (vVisible == 0) {
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

  g_u_view_projection = glGetUniformLocation(g_program, "uViewProjection");
  g_u_object = glGetUniformLocation(g_program, "uObject");
  g_u_point_size = glGetUniformLocation(g_program, "uPointSize");
  g_u_use_override_color = glGetUniformLocation(g_program, "uUseOverrideColor");
  g_u_override_color = glGetUniformLocation(g_program, "uOverrideColor");
  g_u_clip_enabled = glGetUniformLocation(g_program, "uClipEnabled");
  g_u_clip_center = glGetUniformLocation(g_program, "uClipCenter");
  g_u_clip_half_extents = glGetUniformLocation(g_program, "uClipHalfExtents");
  g_u_clip_axis_x = glGetUniformLocation(g_program, "uClipAxisX");
  g_u_clip_axis_y = glGetUniformLocation(g_program, "uClipAxisY");
  g_u_clip_axis_z = glGetUniformLocation(g_program, "uClipAxisZ");
  return g_program != 0;
}

void CleanupRenderer() {
  for (PointCloudObject& object : g_pointcloud_objects) {
    ReleaseObjectGPUResources(object);
  }
  if (g_program != 0) glDeleteProgram(g_program);
  g_program = 0;
}

bool UploadObjectPointDataIfNeeded(PointCloudObject& object) {
  if (!object.gpu_dirty) {
    return object.vao != 0 && object.vbo != 0;
  }

  if (!InitializeRenderer()) {
    return false;
  }

  if (object.vao == 0) {
    glGenVertexArrays(1, &object.vao);
  }
  if (object.vbo == 0) {
    glGenBuffers(1, &object.vbo);
  }
  if (object.vao == 0 || object.vbo == 0) {
    LogMessage("Failed to allocate point cloud object buffers for '%s'.", object.id.c_str());
    return false;
  }

  glBindVertexArray(object.vao);
  glBindBuffer(GL_ARRAY_BUFFER, object.vbo);
  glBufferData(GL_ARRAY_BUFFER,
      static_cast<GLsizeiptr>(object.points.size() * sizeof(float)),
      object.points.empty() ? nullptr : object.points.data(),
      GL_STATIC_DRAW);

  glEnableVertexAttribArray(0);
  glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 7 * sizeof(float), reinterpret_cast<void*>(0));
  glEnableVertexAttribArray(1);
  glVertexAttribPointer(1, 4, GL_FLOAT, GL_FALSE, 7 * sizeof(float), reinterpret_cast<void*>(3 * sizeof(float)));

  glBindBuffer(GL_ARRAY_BUFFER, 0);
  glBindVertexArray(0);
  object.gpu_point_count = static_cast<GLsizei>(object.points.size() / 7);
  object.gpu_dirty = false;
  return true;
}

void SetClipUniforms(const ClipBoxState& clip_box) {
  glUniform1i(g_u_clip_enabled, clip_box.enabled ? 1 : 0);
  if (!clip_box.enabled) {
    return;
  }
  glUniform3f(g_u_clip_center,
      static_cast<float>(clip_box.center_xyz[0]),
      static_cast<float>(clip_box.center_xyz[1]),
      static_cast<float>(clip_box.center_xyz[2]));
  glUniform3f(g_u_clip_half_extents,
      static_cast<float>(clip_box.half_extents_xyz[0]),
      static_cast<float>(clip_box.half_extents_xyz[1]),
      static_cast<float>(clip_box.half_extents_xyz[2]));
  glUniform3f(g_u_clip_axis_x,
      static_cast<float>(clip_box.axes_xyz[0]),
      static_cast<float>(clip_box.axes_xyz[1]),
      static_cast<float>(clip_box.axes_xyz[2]));
  glUniform3f(g_u_clip_axis_y,
      static_cast<float>(clip_box.axes_xyz[3]),
      static_cast<float>(clip_box.axes_xyz[4]),
      static_cast<float>(clip_box.axes_xyz[5]));
  glUniform3f(g_u_clip_axis_z,
      static_cast<float>(clip_box.axes_xyz[6]),
      static_cast<float>(clip_box.axes_xyz[7]),
      static_cast<float>(clip_box.axes_xyz[8]));
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

bool ShouldRenderNativeHighlight(const PointCloudObject& object) {
  return static_cast<GLsizei>(object.points.size() / 7) <= kHeavyHighlightPointThreshold;
}

bool LoadObjectFromGaspFile(
    PointCloudObject& object,
    const char* filename,
    double* out_center_xyz,
    double* out_half_extents_xyz) {
  if (filename == nullptr) {
    return false;
  }

  const std::wstring wide_filename = Utf8ToWide(filename);
  if (wide_filename.empty()) {
    LogMessage("Failed to convert GASP path from UTF-8: %s", filename);
    return false;
  }

  std::ifstream input(wide_filename.c_str(), std::ios::binary);
  if (!input.is_open()) {
    LogMessage("Failed to open GASP file: %s", filename);
    return false;
  }

  GaspHeader header = {};
  if (!ReadGaspHeader(input, &header)) {
    LogMessage("Failed to read GASP header: %s", filename);
    return false;
  }

  if (std::memcmp(header.magic, kGaspMagic, sizeof(kGaspMagic)) != 0) {
    LogMessage("Invalid GASP magic: %s", filename);
    return false;
  }
  if (header.version != kGaspVersion) {
    LogMessage("Unsupported GASP version %u: %s", header.version, filename);
    return false;
  }
  if (header.full_stride != kGaspPointStride) {
    LogMessage("Unsupported GASP point stride %u: %s", header.full_stride, filename);
    return false;
  }
  if (header.preview_count > 0 && header.preview_stride != kGaspPointStride) {
    LogMessage("Unsupported GASP preview stride %u: %s", header.preview_stride, filename);
    return false;
  }
  if (header.full_count == 0) {
    LogMessage("GASP file contains no points: %s", filename);
    return false;
  }
  if (header.full_count > (static_cast<std::uint64_t>((std::numeric_limits<size_t>::max)()) / kGaspPointStride)) {
    LogMessage("GASP point count is too large: %s", filename);
    return false;
  }

  if (!SkipBytes(input, header.name_length) || !SkipBytes(input, header.source_length)) {
    LogMessage("Failed to skip GASP metadata strings: %s", filename);
    return false;
  }

  const size_t point_value_count = static_cast<size_t>(header.full_count) * static_cast<size_t>(header.full_stride);
  object.points.resize(point_value_count);
  const std::uint64_t point_buffer_bytes =
      static_cast<std::uint64_t>(point_value_count) * static_cast<std::uint64_t>(sizeof(float));
  if (point_buffer_bytes > static_cast<std::uint64_t>((std::numeric_limits<std::streamsize>::max)()) ||
      !ReadExact(input, object.points.data(), static_cast<size_t>(point_buffer_bytes))) {
    LogMessage("Failed to read GASP point buffer: %s", filename);
    object.points.clear();
    return false;
  }

  const std::uint64_t preview_bytes =
      header.preview_count *
      static_cast<std::uint64_t>(header.preview_stride) *
      static_cast<std::uint64_t>(sizeof(float));
  if (!SkipBytes(input, preview_bytes)) {
    LogMessage("Failed to skip GASP preview buffer: %s", filename);
    object.points.clear();
    return false;
  }

  NormalizeLoadedPointColorsIfNeeded(object);

  object.base_half_extents[0] = static_cast<float>(header.half_extents_xyz[0] > 0.001 ? header.half_extents_xyz[0] : 0.001);
  object.base_half_extents[1] = static_cast<float>(header.half_extents_xyz[1] > 0.001 ? header.half_extents_xyz[1] : 0.001);
  object.base_half_extents[2] = static_cast<float>(header.half_extents_xyz[2] > 0.001 ? header.half_extents_xyz[2] : 0.001);
  object.center_xyz[0] = header.center_xyz[0];
  object.center_xyz[1] = header.center_xyz[1];
  object.center_xyz[2] = header.center_xyz[2];
  object.half_extents_xyz[0] = header.half_extents_xyz[0];
  object.half_extents_xyz[1] = header.half_extents_xyz[1];
  object.half_extents_xyz[2] = header.half_extents_xyz[2];
  ResetObjectTransform(object);
  object.gpu_dirty = true;
  object.visible = true;

  if (out_center_xyz != nullptr) {
    out_center_xyz[0] = header.center_xyz[0];
    out_center_xyz[1] = header.center_xyz[1];
    out_center_xyz[2] = header.center_xyz[2];
  }
  if (out_half_extents_xyz != nullptr) {
    out_half_extents_xyz[0] = header.half_extents_xyz[0];
    out_half_extents_xyz[1] = header.half_extents_xyz[1];
    out_half_extents_xyz[2] = header.half_extents_xyz[2];
  }

  return true;
}

void DrawPointCloudObject(
    const PointCloudObject& object,
    const ClipBoxState& clip_box,
    const float* view_projection,
    bool highlight_pass,
    float point_size) {
  PointCloudObject& mutable_object = const_cast<PointCloudObject&>(object);
  if (!UploadObjectPointDataIfNeeded(mutable_object) || mutable_object.gpu_point_count <= 0) {
    return;
  }

  float object_matrix[16] = {0.0f};
  BuildObjectMatrix(object, object_matrix);
  float r = 0.0f;
  float g = 0.0f;
  float b = 0.0f;
  float a = 1.0f;
  if (highlight_pass) {
    HighlightColorForMode(object.highlight_mode, &r, &g, &b, &a);
  }

  glUniformMatrix4fv(g_u_view_projection, 1, GL_TRUE, view_projection);
  glUniformMatrix4fv(g_u_object, 1, GL_TRUE, object_matrix);
  glUniform1f(g_u_point_size, point_size);
  glUniform1i(g_u_use_override_color, highlight_pass ? 1 : 0);
  glUniform4f(g_u_override_color, r, g, b, a);
  SetClipUniforms(clip_box);

  glBindVertexArray(mutable_object.vao);
  glDrawArrays(GL_POINTS, 0, mutable_object.gpu_point_count);
  glBindVertexArray(0);
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
  object.gpu_dirty = true;
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
  LogMessage("SetPointCloudObjectData stored %d points for '%s'.", count, object.id.c_str());
  return 1;
}

extern "C" EXPORT int LoadPointCloudObjectFromGasp(
    const char* object_id,
    const char* filename,
    double* out_center_xyz,
    double* out_half_extents_xyz) {
  if (object_id == nullptr || filename == nullptr) {
    return 0;
  }

  PointCloudObject& object = UpsertObject(object_id);
  ReleaseObjectGPUResources(object);
  object.points.clear();
  object.id = object_id;
  if (!LoadObjectFromGaspFile(object, filename, out_center_xyz, out_half_extents_xyz)) {
    object.points.clear();
    g_data_ready = !g_pointcloud_objects.empty();
    return 0;
  }

  g_data_ready = !g_pointcloud_objects.empty();
  LogMessage("Loaded GASP project '%s' for '%s' (%d points).",
      filename,
      object.id.c_str(),
      static_cast<int>(object.points.size() / 7));
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

  for (auto it = g_pointcloud_objects.begin(); it != g_pointcloud_objects.end(); ++it) {
    if (it->id != object_id) {
      continue;
    }

    ReleaseObjectGPUResources(*it);
    g_pointcloud_objects.erase(it);
    g_data_ready = !g_pointcloud_objects.empty();
    return 1;
  }

  return 0;
}

extern "C" EXPORT void ClearPointCloudObjects() {
  for (PointCloudObject& object : g_pointcloud_objects) {
    ReleaseObjectGPUResources(object);
  }
  g_pointcloud_objects.clear();
  g_data_ready = false;
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

  float view_projection[16] = {0.0f};
  MultiplyMat4(projection, view, view_projection);

  GLint old_program = 0;
  GLint old_vao = 0;
  GLint old_array_buffer = 0;
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
  glEnable(GL_PROGRAM_POINT_SIZE);
  glDisable(GL_TEXTURE_2D);
  glUseProgram(g_program);
  glBindBuffer(GL_ARRAY_BUFFER, 0);

  GLsizei point_count = 0;
  for (const PointCloudObject& object : g_pointcloud_objects) {
    if (!object.visible) {
      continue;
    }

    point_count += static_cast<GLsizei>(object.points.size() / 7);
    DrawPointCloudObject(object, clip_box, view_projection, false, 4.0f);
  }
  LogMessage("Rendering %d points.", point_count);

  bool has_highlighted_objects = false;
  for (const PointCloudObject& object : g_pointcloud_objects) {
    if (object.visible &&
        object.highlight_mode != kHighlightNone &&
        ShouldRenderNativeHighlight(object)) {
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
      if (!object.visible ||
          object.highlight_mode == kHighlightNone ||
          !ShouldRenderNativeHighlight(object)) {
        continue;
      }

      DrawPointCloudObject(object, clip_box, view_projection, false, HighlightMaskPointSizeForMode(object.highlight_mode));
    }

    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glStencilMask(0x00);
    glStencilFunc(GL_NOTEQUAL, 0x80, 0x80);
    glStencilOp(GL_KEEP, GL_KEEP, GL_KEEP);
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);

    for (const PointCloudObject& object : g_pointcloud_objects) {
      if (!object.visible ||
          object.highlight_mode == kHighlightNone ||
          !ShouldRenderNativeHighlight(object)) {
        continue;
      }

      DrawPointCloudObject(object, clip_box, view_projection, true, HighlightOutlinePointSizeForMode(object.highlight_mode));
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
