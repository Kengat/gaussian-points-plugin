// SketchUpOverlayBridge.cpp
// Registers a minimal Live C API overlay from Ruby and reuses its frame callback
// to capture SketchUp camera matrices for GaussianSplatRenderer.dll.

#include "SketchUpOverlayBridge.h"

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <fstream>
#include <algorithm>
#include <cmath>
#include <string>

#include <GL/glew.h>
#include <GL/gl.h>
#include <MinHook.h>

#include <SketchUpAPI/application/application.h>
#include <SketchUpAPI/application/model.h>
#include <SketchUpAPI/application/overlay.h>

extern "C" IMAGE_DOS_HEADER __ImageBase;

namespace {

float g_view[16] = { 0.0f };
float g_proj[16] = { 0.0f };
float g_camera_position[3] = { 0.0f, 0.0f, 0.0f };
float g_camera_target[3] = { 0.0f, 0.0f, 0.0f };
float g_camera_up[3] = { 0.0f, 1.0f, 0.0f };
bool g_camera_is_perspective = true;
bool g_overlay_registered = false;
SUOverlayRef g_overlay = SU_INVALID;
bool g_frame_pending = false;

struct ClipBoxState {
  bool enabled = false;
  bool visible = false;
  bool gizmo_visible = false;
  double min_xyz[3] = { 0.0, 0.0, 0.0 };
  double max_xyz[3] = { 0.0, 0.0, 0.0 };
  int hovered_handle = 0;
  int active_handle = 0;
};

ClipBoxState g_clip_box = {};

constexpr int kHandleNone = 0;
constexpr int kHandleMoveX = 1;
constexpr int kHandleMoveY = 2;
constexpr int kHandleMoveZ = 3;
constexpr int kHandleResizeMinX = 4;
constexpr int kHandleResizeMaxX = 5;
constexpr int kHandleResizeMinY = 6;
constexpr int kHandleResizeMaxY = 7;
constexpr int kHandleResizeMinZ = 8;
constexpr int kHandleResizeMaxZ = 9;
constexpr int kHandleMovePlaneXY = 10;
constexpr int kHandleMovePlaneXZ = 11;
constexpr int kHandleMovePlaneYZ = 12;

using PFN_RENDER_POINTCLOUD = void (*)();
using PFN_SET_POINTCLOUD = void (*)(const double*, int);
PFN_RENDER_POINTCLOUD g_gaussian_render = nullptr;
PFN_RENDER_POINTCLOUD g_pointcloud_render = nullptr;
PFN_SET_POINTCLOUD g_pointcloud_set_data = nullptr;
using PFN_SWAPBUFFERS = BOOL(WINAPI*)(HDC);
PFN_SWAPBUFFERS g_orig_swap_buffers = nullptr;
bool g_swap_hook_installed = false;
thread_local bool g_inside_swap_hook = false;

constexpr const char* kOverlayId = "kengat.gaussian_points.overlay";
constexpr const char* kOverlayName = "Gaussian Points Overlay";
constexpr const char* kOverlayDesc = "Feeds SketchUp camera matrices to Gaussian splat renderer";
constexpr const char* kOverlaySource = "Gaussian Points Sandbox";

void LogBridge(const char* message) {
  OutputDebugStringA("[OverlayBridge] ");
  OutputDebugStringA(message);
  OutputDebugStringA("\n");

  char tempPath[MAX_PATH] = {};
  DWORD length = GetTempPathA(MAX_PATH, tempPath);
  if (length == 0 || length > MAX_PATH) {
    return;
  }

  std::ofstream logFile(std::string(tempPath) + "gaussian_splats_native.log", std::ios::app);
  if (logFile.is_open()) {
    logFile << "[OverlayBridge] " << message << "\n";
  }
}

void TransposeMat4(const float* input, float* output) {
  for (int row = 0; row < 4; ++row) {
    for (int col = 0; col < 4; ++col) {
      output[col * 4 + row] = input[row * 4 + col];
    }
  }
}

bool IsHandleHighlighted(int handle_id) {
  return handle_id != kHandleNone &&
      (g_clip_box.hovered_handle == handle_id || g_clip_box.active_handle == handle_id);
}

struct Vec3 {
  float x;
  float y;
  float z;
};

Vec3 MakeVec3(float x, float y, float z) {
  Vec3 result = { x, y, z };
  return result;
}

Vec3 Add(const Vec3& a, const Vec3& b) {
  return MakeVec3(a.x + b.x, a.y + b.y, a.z + b.z);
}

Vec3 Subtract(const Vec3& a, const Vec3& b) {
  return MakeVec3(a.x - b.x, a.y - b.y, a.z - b.z);
}

Vec3 Scale(const Vec3& v, float scalar) {
  return MakeVec3(v.x * scalar, v.y * scalar, v.z * scalar);
}

float Dot(const Vec3& a, const Vec3& b) {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

Vec3 Cross(const Vec3& a, const Vec3& b) {
  return MakeVec3(
      a.y * b.z - a.z * b.y,
      a.z * b.x - a.x * b.z,
      a.x * b.y - a.y * b.x);
}

float Length(const Vec3& v) {
  return sqrtf(Dot(v, v));
}

Vec3 Normalize(const Vec3& v) {
  const float length = Length(v);
  if (length < 1.0e-5f) {
    return MakeVec3(0.0f, 0.0f, 0.0f);
  }
  return Scale(v, 1.0f / length);
}

float MaxFloat(float a, float b) {
  return a > b ? a : b;
}

float ClampFloat(float value, float min_value, float max_value) {
  if (value < min_value) return min_value;
  if (value > max_value) return max_value;
  return value;
}

Vec3 CameraPosition() {
  return MakeVec3(g_camera_position[0], g_camera_position[1], g_camera_position[2]);
}

Vec3 CameraTarget() {
  return MakeVec3(g_camera_target[0], g_camera_target[1], g_camera_target[2]);
}

Vec3 CameraUp() {
  return Normalize(MakeVec3(g_camera_up[0], g_camera_up[1], g_camera_up[2]));
}

Vec3 CameraForward() {
  Vec3 forward = Normalize(Subtract(CameraTarget(), CameraPosition()));
  if (Length(forward) < 1.0e-5f) {
    forward = MakeVec3(0.0f, 0.0f, -1.0f);
  }
  return forward;
}

Vec3 CameraRight() {
  Vec3 right = Normalize(Cross(CameraForward(), CameraUp()));
  if (Length(right) < 1.0e-5f) {
    right = Normalize(Cross(CameraForward(), MakeVec3(0.0f, 0.0f, 1.0f)));
  }
  if (Length(right) < 1.0e-5f) {
    right = MakeVec3(1.0f, 0.0f, 0.0f);
  }
  return right;
}

Vec3 CameraBillboardUp() {
  Vec3 up = Normalize(Cross(CameraRight(), CameraForward()));
  if (Length(up) < 1.0e-5f) {
    up = MakeVec3(0.0f, 1.0f, 0.0f);
  }
  return up;
}

Vec3 AxisVector(char axis) {
  switch (axis) {
  case 'x': return MakeVec3(1.0f, 0.0f, 0.0f);
  case 'y': return MakeVec3(0.0f, 1.0f, 0.0f);
  case 'z': return MakeVec3(0.0f, 0.0f, 1.0f);
  default: return MakeVec3(0.0f, 0.0f, 0.0f);
  }
}

Vec3 BoxMinVec() {
  return MakeVec3(
      static_cast<float>(g_clip_box.min_xyz[0]),
      static_cast<float>(g_clip_box.min_xyz[1]),
      static_cast<float>(g_clip_box.min_xyz[2]));
}

Vec3 BoxMaxVec() {
  return MakeVec3(
      static_cast<float>(g_clip_box.max_xyz[0]),
      static_cast<float>(g_clip_box.max_xyz[1]),
      static_cast<float>(g_clip_box.max_xyz[2]));
}

Vec3 BoxCenterVec() {
  const Vec3 box_min = BoxMinVec();
  const Vec3 box_max = BoxMaxVec();
  return MakeVec3(
      (box_min.x + box_max.x) * 0.5f,
      (box_min.y + box_max.y) * 0.5f,
      (box_min.z + box_max.z) * 0.5f);
}

Vec3 AxisFaceCenter(char axis, bool positive_side) {
  const Vec3 box_min = BoxMinVec();
  const Vec3 box_max = BoxMaxVec();
  const Vec3 center = BoxCenterVec();

  switch (axis) {
  case 'x':
    return MakeVec3(positive_side ? box_max.x : box_min.x, center.y, center.z);
  case 'y':
    return MakeVec3(center.x, positive_side ? box_max.y : box_min.y, center.z);
  case 'z':
    return MakeVec3(center.x, center.y, positive_side ? box_max.z : box_min.z);
  default:
    return center;
  }
}

float ProjectionScaleY() {
  const float scale = fabsf(g_proj[5]);
  return scale > 1.0e-5f ? scale : 1.0f;
}

float WorldUnitsPerPixelAt(const Vec3& point) {
  GLint viewport[4] = {};
  glGetIntegerv(GL_VIEWPORT, viewport);
  const float viewport_height = viewport[3] > 0 ? static_cast<float>(viewport[3]) : 1.0f;

  if (g_camera_is_perspective) {
    const float depth = MaxFloat(Dot(Subtract(point, CameraPosition()), CameraForward()), 1.0f);
    return (2.0f * depth / ProjectionScaleY()) / viewport_height;
  }

  return (2.0f / ProjectionScaleY()) / viewport_height;
}

float PixelsToWorldAt(const Vec3& point, float pixels) {
  return WorldUnitsPerPixelAt(point) * pixels;
}

void DrawVertex(const Vec3& point) {
  glVertex3f(point.x, point.y, point.z);
}

Vec3 ResizeHandleCenterForId(int handle_id) {
  switch (handle_id) {
  case kHandleResizeMinX: return AxisFaceCenter('x', false);
  case kHandleResizeMaxX: return AxisFaceCenter('x', true);
  case kHandleResizeMinY: return AxisFaceCenter('y', false);
  case kHandleResizeMaxY: return AxisFaceCenter('y', true);
  case kHandleResizeMinZ: return AxisFaceCenter('z', false);
  case kHandleResizeMaxZ: return AxisFaceCenter('z', true);
  default: return BoxCenterVec();
  }
}

void MoveHandleGeometry(char axis, Vec3* line_start, Vec3* line_tip) {
  const Vec3 face_center = AxisFaceCenter(axis, true);
  const Vec3 axis_dir = AxisVector(axis);
  const float gap = PixelsToWorldAt(face_center, 18.0f);
  const float length = PixelsToWorldAt(face_center, 54.0f);

  *line_start = Add(face_center, Scale(axis_dir, gap));
  *line_tip = Add(face_center, Scale(axis_dir, gap + length));
}

void PlaneHandleGeometry(char axis_a_name, char axis_b_name, Vec3* center, float* half_size) {
  const Vec3 box_center = BoxCenterVec();
  const Vec3 axis_a = AxisVector(axis_a_name);
  const Vec3 axis_b = AxisVector(axis_b_name);
  const float local_half_size = PixelsToWorldAt(box_center, 9.0f);
  const float offset = PixelsToWorldAt(box_center, 16.0f) + local_half_size;

  *center = Add(Add(box_center, Scale(axis_a, offset)), Scale(axis_b, offset));
  *half_size = local_half_size;
}

void DrawBillboardSquare(const Vec3& center, float half_size, float r, float g, float b, bool highlighted) {
  const Vec3 right = Scale(CameraRight(), half_size);
  const Vec3 up = Scale(CameraBillboardUp(), half_size);
  const Vec3 corners[4] = {
    Subtract(Subtract(center, right), up),
    Add(Subtract(center, up), right),
    Add(Add(center, right), up),
    Add(Subtract(center, right), up)
  };

  glColor4f(0.0f, 0.0f, 0.0f, highlighted ? 0.28f : 0.18f);
  glBegin(GL_QUADS);
  for (int i = 0; i < 4; ++i) {
    DrawVertex(corners[i]);
  }
  glEnd();

  glLineWidth(highlighted ? 2.5f : 1.5f);
  glColor4f(r, g, b, 1.0f);
  glBegin(GL_LINE_LOOP);
  for (int i = 0; i < 4; ++i) {
    DrawVertex(corners[i]);
  }
  glEnd();
}

void DrawPlanarDisc(
    const Vec3& center,
    const Vec3& axis_a,
    const Vec3& axis_b,
    float radius,
    float r,
    float g,
    float b,
    bool highlighted) {
  constexpr int kSegments = 24;

  glColor4f(r, g, b, highlighted ? 0.34f : 0.22f);
  glBegin(GL_TRIANGLE_FAN);
  DrawVertex(center);
  for (int segment = 0; segment <= kSegments; ++segment) {
    const float angle = static_cast<float>(segment) / static_cast<float>(kSegments) * 6.28318530718f;
    const Vec3 point = Add(
        Add(center, Scale(axis_a, cosf(angle) * radius)),
        Scale(axis_b, sinf(angle) * radius));
    DrawVertex(point);
  }
  glEnd();

  glLineWidth(highlighted ? 3.0f : 1.5f);
  glColor4f(r, g, b, 1.0f);
  glBegin(GL_LINE_LOOP);
  for (int segment = 0; segment < kSegments; ++segment) {
    const float angle = static_cast<float>(segment) / static_cast<float>(kSegments) * 6.28318530718f;
    const Vec3 point = Add(
        Add(center, Scale(axis_a, cosf(angle) * radius)),
        Scale(axis_b, sinf(angle) * radius));
    DrawVertex(point);
  }
  glEnd();
}

void HandleColorForId(int handle_id, float* r, float* g, float* b) {
  float color[3] = { 1.0f, 0.66f, 0.15f };
  switch (handle_id) {
  case kHandleMoveX:
  case kHandleResizeMinX:
  case kHandleResizeMaxX:
    color[0] = 0.86f; color[1] = 0.27f; color[2] = 0.22f;
    break;
  case kHandleMoveY:
  case kHandleResizeMinY:
  case kHandleResizeMaxY:
    color[0] = 0.27f; color[1] = 0.74f; color[2] = 0.35f;
    break;
  case kHandleMoveZ:
  case kHandleResizeMinZ:
  case kHandleResizeMaxZ:
    color[0] = 0.28f; color[1] = 0.47f; color[2] = 0.90f;
    break;
  case kHandleMovePlaneXY:
    color[0] = 0.91f; color[1] = 0.64f; color[2] = 0.21f;
    break;
  case kHandleMovePlaneXZ:
    color[0] = 0.74f; color[1] = 0.38f; color[2] = 0.74f;
    break;
  case kHandleMovePlaneYZ:
    color[0] = 0.29f; color[1] = 0.71f; color[2] = 0.77f;
    break;
  }

  if (IsHandleHighlighted(handle_id)) {
    color[0] = ClampFloat(color[0] + 0.15f, 0.0f, 1.0f);
    color[1] = ClampFloat(color[1] + 0.15f, 0.0f, 1.0f);
    color[2] = ClampFloat(color[2] + 0.15f, 0.0f, 1.0f);
  }

  *r = color[0];
  *g = color[1];
  *b = color[2];
}

void DrawBoxOverlay(bool draw_shell, bool draw_gizmo) {
  if (!g_clip_box.enabled || !g_clip_box.visible || (!draw_shell && !draw_gizmo)) {
    return;
  }

  const Vec3 box_min = BoxMinVec();
  const Vec3 box_max = BoxMaxVec();
  const Vec3 corners[8] = {
    MakeVec3(box_min.x, box_min.y, box_min.z),
    MakeVec3(box_max.x, box_min.y, box_min.z),
    MakeVec3(box_max.x, box_max.y, box_min.z),
    MakeVec3(box_min.x, box_max.y, box_min.z),
    MakeVec3(box_min.x, box_min.y, box_max.z),
    MakeVec3(box_max.x, box_min.y, box_max.z),
    MakeVec3(box_max.x, box_max.y, box_max.z),
    MakeVec3(box_min.x, box_max.y, box_max.z)
  };

  const int edge_indices[12][2] = {
    { 0, 1 }, { 1, 2 }, { 2, 3 }, { 3, 0 },
    { 4, 5 }, { 5, 6 }, { 6, 7 }, { 7, 4 },
    { 0, 4 }, { 1, 5 }, { 2, 6 }, { 3, 7 }
  };

  float projection_gl[16] = {};
  float view_gl[16] = {};
  TransposeMat4(g_proj, projection_gl);
  TransposeMat4(g_view, view_gl);

  GLint old_matrix_mode = 0;
  GLint old_texture = 0;
  GLint old_program = 0;
  GLint old_depth_func = GL_LEQUAL;
  GLfloat old_line_width = 1.0f;
  GLfloat old_point_size = 1.0f;
  GLboolean depth_enabled = glIsEnabled(GL_DEPTH_TEST);
  GLboolean old_depth_mask = GL_TRUE;
  GLboolean blend_enabled = glIsEnabled(GL_BLEND);
  GLboolean texture_enabled = glIsEnabled(GL_TEXTURE_2D);
  GLboolean cull_enabled = glIsEnabled(GL_CULL_FACE);

  glGetIntegerv(GL_MATRIX_MODE, &old_matrix_mode);
  glGetIntegerv(GL_TEXTURE_BINDING_2D, &old_texture);
  glGetIntegerv(GL_CURRENT_PROGRAM, &old_program);
  glGetIntegerv(GL_DEPTH_FUNC, &old_depth_func);
  glGetBooleanv(GL_DEPTH_WRITEMASK, &old_depth_mask);
  glGetFloatv(GL_LINE_WIDTH, &old_line_width);
  glGetFloatv(GL_POINT_SIZE, &old_point_size);

  glUseProgram(0);
  glDisable(GL_CULL_FACE);
  if (draw_shell) {
    glEnable(GL_DEPTH_TEST);
    glDepthFunc(GL_LEQUAL);
    glDepthMask(GL_FALSE);
  } else {
    glEnable(GL_DEPTH_TEST);
    glDepthFunc(GL_LEQUAL);
    glDepthMask(GL_TRUE);
    glClear(GL_DEPTH_BUFFER_BIT);
  }
  glEnable(GL_BLEND);
  glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
  glDisable(GL_TEXTURE_2D);
  glDisable(GL_PROGRAM_POINT_SIZE);

  glMatrixMode(GL_PROJECTION);
  glPushMatrix();
  glLoadMatrixf(projection_gl);
  glMatrixMode(GL_MODELVIEW);
  glPushMatrix();
  glLoadMatrixf(view_gl);

  if (draw_shell) {
    glColor4f(1.0f, 0.65f, 0.15f, 0.06f);
    glBegin(GL_QUADS);
    DrawVertex(corners[0]); DrawVertex(corners[1]); DrawVertex(corners[2]); DrawVertex(corners[3]);
    DrawVertex(corners[4]); DrawVertex(corners[5]); DrawVertex(corners[6]); DrawVertex(corners[7]);
    DrawVertex(corners[0]); DrawVertex(corners[1]); DrawVertex(corners[5]); DrawVertex(corners[4]);
    DrawVertex(corners[1]); DrawVertex(corners[2]); DrawVertex(corners[6]); DrawVertex(corners[5]);
    DrawVertex(corners[2]); DrawVertex(corners[3]); DrawVertex(corners[7]); DrawVertex(corners[6]);
    DrawVertex(corners[3]); DrawVertex(corners[0]); DrawVertex(corners[4]); DrawVertex(corners[7]);
    glEnd();

    glLineWidth(4.0f);
    glColor4f(0.0f, 0.0f, 0.0f, 0.18f);
    glBegin(GL_LINES);
    for (const auto& edge : edge_indices) {
      DrawVertex(corners[edge[0]]);
      DrawVertex(corners[edge[1]]);
    }
    glEnd();

    glLineWidth(2.0f);
    glColor4f(1.0f, 0.70f, 0.20f, 0.95f);
    glBegin(GL_LINES);
    for (const auto& edge : edge_indices) {
      DrawVertex(corners[edge[0]]);
      DrawVertex(corners[edge[1]]);
    }
    glEnd();
  }

  if (draw_gizmo && g_clip_box.gizmo_visible) {
    const int move_ids[3] = { kHandleMoveX, kHandleMoveY, kHandleMoveZ };
    const char move_axes[3] = { 'x', 'y', 'z' };

    for (int i = 0; i < 3; ++i) {
      Vec3 line_start = {};
      Vec3 line_tip = {};
      MoveHandleGeometry(move_axes[i], &line_start, &line_tip);

      float r = 0.0f;
      float g = 0.0f;
      float b = 0.0f;
      HandleColorForId(move_ids[i], &r, &g, &b);

      const Vec3 axis_dir = AxisVector(move_axes[i]);
      Vec3 side = Normalize(Cross(axis_dir, CameraForward()));
      if (Length(side) < 1.0e-5f) {
        side = Normalize(Cross(axis_dir, CameraBillboardUp()));
      }
      if (Length(side) < 1.0e-5f) {
        side = Normalize(Cross(axis_dir, MakeVec3(0.0f, 0.0f, 1.0f)));
      }
      if (Length(side) < 1.0e-5f) {
        side = MakeVec3(0.0f, 1.0f, 0.0f);
      }

      const float arrow_length = PixelsToWorldAt(line_tip, 12.0f);
      const Vec3 arrow_back = Scale(axis_dir, arrow_length);
      const Vec3 arrow_side = Scale(side, arrow_length * 0.55f);
      const Vec3 left_wing = Add(Subtract(line_tip, arrow_back), arrow_side);
      const Vec3 right_wing = Subtract(Subtract(line_tip, arrow_back), arrow_side);

      glLineWidth(IsHandleHighlighted(move_ids[i]) ? 5.0f : 3.0f);
      glColor4f(0.0f, 0.0f, 0.0f, 0.18f);
      glBegin(GL_LINES);
      DrawVertex(line_start); DrawVertex(line_tip);
      DrawVertex(line_tip); DrawVertex(left_wing);
      DrawVertex(line_tip); DrawVertex(right_wing);
      glEnd();

      glLineWidth(IsHandleHighlighted(move_ids[i]) ? 3.0f : 2.0f);
      glColor4f(r, g, b, 1.0f);
      glBegin(GL_LINES);
      DrawVertex(line_start); DrawVertex(line_tip);
      DrawVertex(line_tip); DrawVertex(left_wing);
      DrawVertex(line_tip); DrawVertex(right_wing);
      glEnd();
    }

    const int plane_ids[3] = { kHandleMovePlaneXY, kHandleMovePlaneXZ, kHandleMovePlaneYZ };
    for (int i = 0; i < 3; ++i) {
      char axis_a = 'x';
      char axis_b = 'y';
      if (plane_ids[i] == kHandleMovePlaneXZ) {
        axis_b = 'z';
      } else if (plane_ids[i] == kHandleMovePlaneYZ) {
        axis_a = 'y';
        axis_b = 'z';
      }

      Vec3 plane_center = {};
      float plane_half_size = 0.0f;
      PlaneHandleGeometry(axis_a, axis_b, &plane_center, &plane_half_size);
      const Vec3 axis_a_vec = AxisVector(axis_a);
      const Vec3 axis_b_vec = AxisVector(axis_b);

      float r = 0.0f;
      float g = 0.0f;
      float b = 0.0f;
      HandleColorForId(plane_ids[i], &r, &g, &b);
      DrawPlanarDisc(
          plane_center,
          axis_a_vec,
          axis_b_vec,
          plane_half_size,
          r,
          g,
          b,
          IsHandleHighlighted(plane_ids[i]));
    }

    const int resize_ids[6] = {
      kHandleResizeMinX, kHandleResizeMaxX,
      kHandleResizeMinY, kHandleResizeMaxY,
      kHandleResizeMinZ, kHandleResizeMaxZ
    };

    for (int i = 0; i < 6; ++i) {
      float r = 0.0f;
      float g = 0.0f;
      float b = 0.0f;
      HandleColorForId(resize_ids[i], &r, &g, &b);
      const Vec3 center = ResizeHandleCenterForId(resize_ids[i]);
      const float half_size = PixelsToWorldAt(center, IsHandleHighlighted(resize_ids[i]) ? 8.0f : 6.0f);
      DrawBillboardSquare(center, half_size, r, g, b, IsHandleHighlighted(resize_ids[i]));
    }
  }

  glPointSize(old_point_size);
  glLineWidth(old_line_width);
  glMatrixMode(GL_MODELVIEW);
  glPopMatrix();
  glMatrixMode(GL_PROJECTION);
  glPopMatrix();
  glMatrixMode(old_matrix_mode);

  glBindTexture(GL_TEXTURE_2D, old_texture);
  glUseProgram(static_cast<GLuint>(old_program));

  if (blend_enabled) {
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
  } else {
    glDisable(GL_BLEND);
  }
  glDepthMask(old_depth_mask);
  glDepthFunc(old_depth_func);
  if (depth_enabled) glEnable(GL_DEPTH_TEST); else glDisable(GL_DEPTH_TEST);
  if (texture_enabled) glEnable(GL_TEXTURE_2D); else glDisable(GL_TEXTURE_2D);
  if (cull_enabled) glEnable(GL_CULL_FACE); else glDisable(GL_CULL_FACE);
}

HMODULE LoadSiblingModule(const char* file_name) {
  HMODULE module = GetModuleHandleA(file_name);
  if (module != nullptr) {
    return module;
  }

  char path[MAX_PATH] = {};
  if (GetModuleFileNameA(reinterpret_cast<HMODULE>(&__ImageBase), path, MAX_PATH) != 0) {
    char* slash = strrchr(path, '\\');
    if (slash != nullptr) {
      *(slash + 1) = '\0';

      const std::string bridge_dir(path);
      const std::string runtime_path = bridge_dir + "runtime\\" + file_name;
      module = LoadLibraryA(runtime_path.c_str());
      if (module != nullptr) {
        return module;
      }

      const std::string sibling_path = bridge_dir + file_name;
      module = LoadLibraryA(sibling_path.c_str());
      if (module != nullptr) {
        return module;
      }
    }
  }

  if (module == nullptr) {
    module = LoadLibraryA(file_name);
  }

  return module;
}

void LoadRenderers() {
  if (g_gaussian_render == nullptr) {
    HMODULE module = LoadSiblingModule("GaussianSplatRenderer.dll");
    if (module == nullptr) {
      LogBridge("GaussianSplatRenderer.dll not found.");
    } else {
      g_gaussian_render = reinterpret_cast<PFN_RENDER_POINTCLOUD>(
          GetProcAddress(module, "renderPointCloud"));
      if (g_gaussian_render == nullptr) {
        LogBridge("Gaussian renderPointCloud export not found.");
      }
    }
  }

  if (g_pointcloud_render == nullptr || g_pointcloud_set_data == nullptr) {
    HMODULE module = LoadSiblingModule("PointCloudRendererDLL.dll");
    if (module == nullptr) {
      LogBridge("PointCloudRendererDLL.dll not found.");
    } else {
      g_pointcloud_render = reinterpret_cast<PFN_RENDER_POINTCLOUD>(
          GetProcAddress(module, "renderPointCloud"));
      g_pointcloud_set_data = reinterpret_cast<PFN_SET_POINTCLOUD>(
          GetProcAddress(module, "SetPointCloud"));
      if (g_pointcloud_render == nullptr || g_pointcloud_set_data == nullptr) {
        LogBridge("Point cloud renderer exports not found.");
      }
    }
  }
}

void LoadRenderer() {
  LoadRenderers();
}

extern "C" __declspec(dllexport) bool GetCurrentMatrices(float* modelview, float* projection) {
  if (modelview == nullptr || projection == nullptr) {
    return false;
  }

  memcpy(modelview, g_view, sizeof(g_view));
  memcpy(projection, g_proj, sizeof(g_proj));
  return true;
}

BOOL WINAPI HookedSwapBuffers(HDC hdc) {
  if (g_orig_swap_buffers == nullptr) {
    return FALSE;
  }

  if (g_inside_swap_hook || !g_frame_pending || wglGetCurrentContext() == nullptr) {
    return g_orig_swap_buffers(hdc);
  }

  g_inside_swap_hook = true;
  LoadRenderers();
  if (g_pointcloud_render != nullptr) {
    g_pointcloud_render();
  }
  if (g_gaussian_render != nullptr) {
    g_gaussian_render();
  }
  DrawBoxOverlay(true, false);
  DrawBoxOverlay(false, true);
  g_frame_pending = false;
  g_inside_swap_hook = false;
  return g_orig_swap_buffers(hdc);
}

void InstallSwapBuffersHook() {
  if (g_swap_hook_installed) {
    return;
  }

  HMODULE gdi32 = GetModuleHandleA("gdi32.dll");
  if (gdi32 == nullptr) {
    LogBridge("gdi32.dll not loaded.");
    return;
  }

  FARPROC target = GetProcAddress(gdi32, "SwapBuffers");
  if (target == nullptr) {
    LogBridge("SwapBuffers export not found.");
    return;
  }

  MH_STATUS status = MH_Initialize();
  if (status != MH_OK && status != MH_ERROR_ALREADY_INITIALIZED) {
    LogBridge("MH_Initialize failed.");
    return;
  }

  status = MH_CreateHook(target, &HookedSwapBuffers,
      reinterpret_cast<LPVOID*>(&g_orig_swap_buffers));
  if (status != MH_OK && status != MH_ERROR_ALREADY_CREATED) {
    LogBridge("MH_CreateHook(SwapBuffers) failed.");
    return;
  }

  status = MH_EnableHook(target);
  if (status != MH_OK && status != MH_ERROR_ENABLED) {
    LogBridge("MH_EnableHook(SwapBuffers) failed.");
    return;
  }

  g_swap_hook_installed = true;
  LogBridge("SwapBuffers hook installed.");
}

void BeginFrame(SUOverlayRef, const SUBeginFrameInfo* info, void*) {
  static bool loggedFirstFrame = false;
  if (info == nullptr) {
    return;
  }

  for (int i = 0; i < 16; ++i) {
    g_view[i] = static_cast<float>(info->view_matrix[i]);
    g_proj[i] = static_cast<float>(info->projection_matrix[i]);
  }

  g_camera_position[0] = static_cast<float>(info->position.x);
  g_camera_position[1] = static_cast<float>(info->position.y);
  g_camera_position[2] = static_cast<float>(info->position.z);
  g_camera_target[0] = static_cast<float>(info->target.x);
  g_camera_target[1] = static_cast<float>(info->target.y);
  g_camera_target[2] = static_cast<float>(info->target.z);
  g_camera_up[0] = static_cast<float>(info->up.x);
  g_camera_up[1] = static_cast<float>(info->up.y);
  g_camera_up[2] = static_cast<float>(info->up.z);
  g_camera_is_perspective = info->is_perspective;

  LoadRenderers();
  if (!loggedFirstFrame) {
    LogBridge("BeginFrame received.");
    loggedFirstFrame = true;
  }
  g_frame_pending = true;
}

void DrawFrame(SUOverlayRef, SUOverlayDrawFrameInfo* info, void*) {
  if (info == nullptr) {
    return;
  }

  info->blending_factor = 0.0;
  info->color.ptr = nullptr;
  info->color.row_pitch = 0;
  info->color.size = 0;
  info->depth.ptr = nullptr;
  info->depth.row_pitch = 0;
  info->depth.size = 0;
  info->reserved = nullptr;
}

void EndFrame(SUOverlayRef, void*) {
  // Nothing to release here. The renderer uses the captured matrices immediately.
}

}  // namespace

extern "C" __declspec(dllexport) bool GetMatrixByLocation(int loc, float* out) {
  if (out == nullptr) {
    return false;
  }

  if (loc == 14) {
    memcpy(out, g_view, sizeof(g_view));
    return true;
  }

  if (loc == 15) {
    memcpy(out, g_proj, sizeof(g_proj));
    return true;
  }

  return false;
}

extern "C" __declspec(dllexport) bool GetCameraState(
    float* position, float* target, float* up, int* is_perspective) {
  if (position == nullptr || target == nullptr || up == nullptr) {
    return false;
  }

  memcpy(position, g_camera_position, sizeof(g_camera_position));
  memcpy(target, g_camera_target, sizeof(g_camera_target));
  memcpy(up, g_camera_up, sizeof(g_camera_up));
  if (is_perspective != nullptr) {
    *is_perspective = g_camera_is_perspective ? 1 : 0;
  }
  return true;
}

extern "C" __declspec(dllexport) void InstallAllHooks() {
  if (g_overlay_registered) {
    return;
  }

  InstallSwapBuffersHook();

  SUModelRef model = SU_INVALID;
  if (SUApplicationGetActiveModel(&model) != SU_ERROR_NONE) {
    LogBridge("SUApplicationGetActiveModel failed.");
    return;
  }

  SUOverlayCreateInfo info = {};
  info.version = SUOVERLAY_CREATE_INFO_VERSION;
  info.id = kOverlayId;
  info.name = kOverlayName;
  info.desc = kOverlayDesc;
  info.source = kOverlaySource;
  info.image_format = SUOVERLAY_IMAGE_FORMAT_RGBA;
  info.image_orientation = SUOVERLAY_IMAGE_ORIENTATION_TOP_DOWN;
  info.begin_frame = BeginFrame;
  info.draw_frame = DrawFrame;
  info.end_frame = EndFrame;

  SUOverlayRef overlay = SU_INVALID;
  if (SUModelCreateOverlay(model, &info, &overlay) != SU_ERROR_NONE) {
    LogBridge("SUModelCreateOverlay failed.");
    return;
  }

  if (SUOverlayEnable(overlay, true) != SU_ERROR_NONE) {
    LogBridge("SUOverlayEnable failed.");
    SUModelReleaseOverlay(model, &overlay);
    return;
  }

  g_overlay = overlay;
  g_overlay_registered = true;
  LogBridge("Overlay registered.");
}

extern "C" __declspec(dllexport) void SetPointCloudData(const double* points, int count) {
  LoadRenderers();
  if (g_pointcloud_set_data == nullptr) {
    LogBridge("Point cloud SetPointCloud export not found.");
    return;
  }

  g_pointcloud_set_data(points, count);
}

extern "C" __declspec(dllexport) void SetClipBoxState(
    int enabled,
    int visible,
    int gizmo_visible,
    const double* min_xyz,
    const double* max_xyz,
    int hovered_handle,
    int active_handle) {
  g_clip_box.enabled = enabled != 0;
  g_clip_box.visible = visible != 0;
  g_clip_box.gizmo_visible = gizmo_visible != 0;

  if (min_xyz != nullptr) {
    memcpy(g_clip_box.min_xyz, min_xyz, sizeof(g_clip_box.min_xyz));
  }
  if (max_xyz != nullptr) {
    memcpy(g_clip_box.max_xyz, max_xyz, sizeof(g_clip_box.max_xyz));
  }

  g_clip_box.hovered_handle = hovered_handle;
  g_clip_box.active_handle = active_handle;
}

extern "C" __declspec(dllexport) bool GetClipBoxState(
    int* enabled,
    double* min_xyz,
    double* max_xyz) {
  if (enabled == nullptr || min_xyz == nullptr || max_xyz == nullptr) {
    return false;
  }

  *enabled = g_clip_box.enabled ? 1 : 0;
  memcpy(min_xyz, g_clip_box.min_xyz, sizeof(g_clip_box.min_xyz));
  memcpy(max_xyz, g_clip_box.max_xyz, sizeof(g_clip_box.max_xyz));
  return true;
}

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) {
    // Avoid LoadLibrary work under the loader lock. The renderer is loaded lazily
    // from BeginFrame after SketchUp has fully initialized the overlay callback.
  }
  if (reason == DLL_PROCESS_DETACH) {
    g_frame_pending = false;
    if (g_swap_hook_installed) {
      MH_DisableHook(MH_ALL_HOOKS);
      MH_Uninitialize();
    }
  }
  return TRUE;
}
