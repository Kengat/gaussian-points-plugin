#include "GaussianSplatRenderer.h"
#include <windows.h>
#include <GL/glew.h>
#include <GL/gl.h>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>
#include <cmath>
#include <algorithm>
#include <map>
#include <numeric>

#define NOMINMAX
#undef min
#undef max
#define M_PI 3.14159265358979323846

// Functions imported from the SketchUp overlay bridge.
typedef bool (*PFN_GET_MATRIX_BY_LOC)(int location, float* matrix);
typedef bool (*PFN_GET_CAMERA_STATE)(float* position, float* target, float* up, int* isPerspective);
typedef bool (*PFN_GET_CLIP_BOX_STATE)(int* enabled, double* center_xyz, double* half_extents_xyz, double* axes_xyz);
static PFN_GET_MATRIX_BY_LOC GetMatrixByLocation = nullptr;
static PFN_GET_CAMERA_STATE GetCameraState = nullptr;
static PFN_GET_CLIP_BOX_STATE GetClipBoxState = nullptr;

struct NativeClipBoxState {
    bool enabled = false;
    double center_xyz[3] = { 0.0, 0.0, 0.0 };
    double half_extents_xyz[3] = { 0.0, 0.0, 0.0 };
    double axes_xyz[9] = {
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
        0.0, 0.0, 1.0
    };
};

// Renderer-owned state for splats, buffers, and camera-driven sorting.
static std::vector<float> g_points;
static bool g_dataReady = false;
static GLuint g_gaussTexture = 0;
static bool g_textureInitialized = false;

// Runtime representation of a single billboarded gaussian splat.
struct GaussSplat {
    float position[3];
    float color[4];    // R, G, B, A
    float scale[2];
    float rotation[4];
    float basis_x[3];
    float basis_y[3];
    int use_custom_basis = 0;
    int highlight_mode = 0;
};
static std::vector<GaussSplat> g_splats;

struct SplatObject {
    std::string id;
    std::vector<GaussSplat> local_splats;
    double center_xyz[3] = { 0.0, 0.0, 0.0 };
    double half_extents_xyz[3] = { 1.0, 1.0, 1.0 };
    double base_half_extents_xyz[3] = { 1.0, 1.0, 1.0 };
    double axes_xyz[9] = {
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
        0.0, 0.0, 1.0
    };
    bool visible = true;
    int highlight_mode = 0;
};
static std::vector<SplatObject> g_splatObjects;
static const int HIGHLIGHT_NONE = 0;
static const int HIGHLIGHT_HOVER = 1;
static const int HIGHLIGHT_SELECTED = 2;
static const int IMPORT_ORIENTATION_LEGACY = 0;
static const int IMPORT_ORIENTATION_SWAP_A = 1;
static const int IMPORT_ORIENTATION_SWAP_B = 2;
static const int IMPORT_ORIENTATION_FLIP_Z = 3;
static const int IMPORT_ORIENTATION_RAW = 4;

enum SplatSortingMode {
    SORT_Z_COORD = 0, SORT_HEMISPHERE = 1, SORT_WEIGHTED_DEPTH = 2,
    SORT_RADIAL_SECTORS = 3, SORT_ADAPTIVE_ANGLE = 4, SORT_FORWARD_PLUS = 5
};
static SplatSortingMode g_sortingMode = SORT_FORWARD_PLUS;

struct SplatVBOData {
    struct VertexData {
        float position[3]; float texCoord[2]; float color[4];
    };
    GLuint vbo = 0; GLuint vao = 0; GLuint ebo = 0;
    std::vector<VertexData> vertices; std::vector<GLuint> indices;
    bool initialized = false; bool needsUpdate = true;
};
static SplatVBOData g_splatVBO;
static SplatVBOData g_highlightMaskVBO;
static SplatVBOData g_outlineVBO;

static std::vector<GLuint> g_splatSortIndices;

struct SplatSortData {
    GLuint index;
    float projValue;
    float distanceSquared;
    float sortKey;
    bool isBackfacing;
};
static std::vector<SplatSortData> g_splatSortCache;

static float g_lastCamPos[3] = { 0, 0, 0 };
static float g_lastViewDir[3] = { 0, 0, 0 };
static int g_framesSinceLastSort = 0;
static const int SORT_EVERY_N_FRAMES = 2;
static NativeClipBoxState g_lastClipBoxState = {};
static bool g_hasLastClipBoxState = false;

static GLuint g_splatShader = 0;
static GLuint g_outlineCompositeShader = 0;
static GLuint g_outlineMaskFBO = 0;
static GLuint g_outlineMaskColorTex = 0;
static GLuint g_outlineMaskDepthTex = 0;
static GLuint g_sceneDepthTex = 0;
static GLuint g_outlineQuadVAO = 0;
static GLuint g_outlineQuadVBO = 0;
static int g_outlineViewportWidth = 0;
static int g_outlineViewportHeight = 0;

static void LogRenderer(const char* format, ...) {
    char buffer[1024]; va_list args; va_start(args, format);
    vsnprintf(buffer, sizeof(buffer), format, args); va_end(args);
    OutputDebugStringA("[RendDBG] "); OutputDebugStringA(buffer); OutputDebugStringA("\n");
    char tempPath[MAX_PATH] = {};
    DWORD length = GetTempPathA(MAX_PATH, tempPath);
    if (length != 0 && length <= MAX_PATH) {
        std::ofstream logFile(std::string(tempPath) + "gaussian_splats_native.log", std::ios::app);
        if (logFile.is_open()) {
            logFile << "[Renderer] " << buffer << "\n";
        }
    }
}

static void PrintRendererMatrix(const char* name, const float* matrix) {
    LogRenderer("Matrix '%s':", name);
    if (!matrix) { LogRenderer("  (null)"); return; }
    for (int i = 0; i < 4; i++) {
        char rowBuf[128];
        snprintf(rowBuf, sizeof(rowBuf), "[%.4f %.4f %.4f %.4f]",
            matrix[i * 4 + 0], matrix[i * 4 + 1], matrix[i * 4 + 2], matrix[i * 4 + 3]);
        LogRenderer("  %s", rowBuf);
    }
}

static void MultiplyMat4(const float* left, const float* right, float* result) {
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

static void TransposeMat4(const float* source, float* result) {
    for (int row = 0; row < 4; ++row) {
        for (int col = 0; col < 4; ++col) {
            result[col * 4 + row] = source[row * 4 + col];
        }
    }
}

static NativeClipBoxState FetchClipBoxStateSnapshot() {
    NativeClipBoxState state;
    if (!GetClipBoxState) {
        return state;
    }

    int enabled = 0;
    if (!GetClipBoxState(&enabled, state.center_xyz, state.half_extents_xyz, state.axes_xyz)) {
        return state;
    }

    state.enabled = enabled != 0;
    return state;
}

static bool ClipStatesEqual(const NativeClipBoxState& a, const NativeClipBoxState& b) {
    if (a.enabled != b.enabled) {
        return false;
    }

    for (int i = 0; i < 3; ++i) {
        if (fabs(a.center_xyz[i] - b.center_xyz[i]) > 1e-6 ||
            fabs(a.half_extents_xyz[i] - b.half_extents_xyz[i]) > 1e-6) {
            return false;
        }
    }

    for (int i = 0; i < 9; ++i) {
        if (fabs(a.axes_xyz[i] - b.axes_xyz[i]) > 1e-6) {
            return false;
        }
    }

    return true;
}

static bool IsPointInsideClipBox(const NativeClipBoxState& clip_box, float x, float y, float z) {
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

    return fabs(local_x) <= clip_box.half_extents_xyz[0] &&
        fabs(local_y) <= clip_box.half_extents_xyz[1] &&
        fabs(local_z) <= clip_box.half_extents_xyz[2];
}

void QuaternionToMatrix(float q0, float q1, float q2, float q3, float matrix[16]) {
    float norm = sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3);
    if (norm < 1e-5f) { memset(matrix, 0, 16 * sizeof(float)); matrix[0] = matrix[5] = matrix[10] = matrix[15] = 1.0f; return; }
    q0 /= norm; q1 /= norm; q2 /= norm; q3 /= norm;
    float q1q1 = q1 * q1, q2q2 = q2 * q2, q3q3 = q3 * q3;
    float q1q2 = q1 * q2, q1q3 = q1 * q3, q2q3 = q2 * q3;
    float q0q1 = q0 * q1, q0q2 = q0 * q2, q0q3 = q0 * q3;
    matrix[0] = 1.0f - 2.0f * (q2q2 + q3q3); matrix[1] = 2.0f * (q1q2 - q0q3);       matrix[2] = 2.0f * (q1q3 + q0q2);       matrix[3] = 0.0f;
    matrix[4] = 2.0f * (q1q2 + q0q3);       matrix[5] = 1.0f - 2.0f * (q1q1 + q3q3); matrix[6] = 2.0f * (q2q3 - q0q1);       matrix[7] = 0.0f;
    matrix[8] = 2.0f * (q1q3 - q0q2);       matrix[9] = 2.0f * (q2q3 + q0q1);       matrix[10] = 1.0f - 2.0f * (q1q1 + q2q2); matrix[11] = 0.0f;
    matrix[12] = 0.0f;                 matrix[13] = 0.0f;                  matrix[14] = 0.0f;                  matrix[15] = 1.0f;
}

static void ComputeBasisVectors(const GaussSplat& splat, float* out_basis_x, float* out_basis_y) {
    if (splat.use_custom_basis != 0) {
        out_basis_x[0] = splat.basis_x[0];
        out_basis_x[1] = splat.basis_x[1];
        out_basis_x[2] = splat.basis_x[2];
        out_basis_y[0] = splat.basis_y[0];
        out_basis_y[1] = splat.basis_y[1];
        out_basis_y[2] = splat.basis_y[2];
        return;
    }

    float rotMatrix[16];
    QuaternionToMatrix(splat.rotation[0], splat.rotation[1], splat.rotation[2], splat.rotation[3], rotMatrix);
    out_basis_x[0] = splat.scale[0] * rotMatrix[0];
    out_basis_x[1] = splat.scale[0] * rotMatrix[1];
    out_basis_x[2] = splat.scale[0] * rotMatrix[2];
    out_basis_y[0] = splat.scale[1] * rotMatrix[4];
    out_basis_y[1] = splat.scale[1] * rotMatrix[5];
    out_basis_y[2] = splat.scale[1] * rotMatrix[6];
}

static void TransformVectorByObject(const SplatObject& object, double sx, double sy, double sz, const float* local_vec, float* out_vec) {
    const double scaled_x = static_cast<double>(local_vec[0]) * sx;
    const double scaled_y = static_cast<double>(local_vec[1]) * sy;
    const double scaled_z = static_cast<double>(local_vec[2]) * sz;
    out_vec[0] = static_cast<float>((object.axes_xyz[0] * scaled_x) + (object.axes_xyz[3] * scaled_y) + (object.axes_xyz[6] * scaled_z));
    out_vec[1] = static_cast<float>((object.axes_xyz[1] * scaled_x) + (object.axes_xyz[4] * scaled_y) + (object.axes_xyz[7] * scaled_z));
    out_vec[2] = static_cast<float>((object.axes_xyz[2] * scaled_x) + (object.axes_xyz[5] * scaled_y) + (object.axes_xyz[8] * scaled_z));
}

static void MarkSplatBuffersDirty() {
    g_splatVBO.needsUpdate = true;
    g_highlightMaskVBO.needsUpdate = true;
    g_outlineVBO.needsUpdate = true;
}

static bool InitializeBufferObjects(SplatVBOData* vbo_data) {
    if (!vbo_data) {
        return false;
    }
    if (vbo_data->initialized) {
        return true;
    }

    glGenVertexArrays(1, &vbo_data->vao);
    glBindVertexArray(vbo_data->vao);
    glGenBuffers(1, &vbo_data->vbo);
    glBindBuffer(GL_ARRAY_BUFFER, vbo_data->vbo);
    glGenBuffers(1, &vbo_data->ebo);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, vbo_data->ebo);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, sizeof(SplatVBOData::VertexData), (void*)offsetof(SplatVBOData::VertexData, position));
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, sizeof(SplatVBOData::VertexData), (void*)offsetof(SplatVBOData::VertexData, texCoord));
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(2, 4, GL_FLOAT, GL_FALSE, sizeof(SplatVBOData::VertexData), (void*)offsetof(SplatVBOData::VertexData, color));
    glEnableVertexAttribArray(2);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);

    vbo_data->initialized = (vbo_data->vao != 0 && vbo_data->vbo != 0 && vbo_data->ebo != 0);
    vbo_data->needsUpdate = true;
    return vbo_data->initialized;
}

static void ClearUploadedBuffer(SplatVBOData* vbo_data) {
    if (!vbo_data || !vbo_data->initialized || vbo_data->vao == 0) {
        return;
    }

    glBindVertexArray(vbo_data->vao);
    glBindBuffer(GL_ARRAY_BUFFER, vbo_data->vbo);
    glBufferData(GL_ARRAY_BUFFER, 0, nullptr, GL_STATIC_DRAW);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, vbo_data->ebo);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, 0, nullptr, GL_STATIC_DRAW);
    glBindVertexArray(0);
    vbo_data->vertices.clear();
    vbo_data->indices.clear();
}

static void HighlightColorForMode(int highlight_mode, float* r, float* g, float* b, float* a) {
    if (highlight_mode == HIGHLIGHT_HOVER) {
        *r = 1.0f; *g = 0.58f; *b = 0.04f; *a = 1.0f;
    }
    else {
        *r = 1.0f; *g = 0.84f; *b = 0.08f; *a = 1.0f;
    }
}

static float HighlightScaleForMode(int highlight_mode) {
    return 1.0f;
}

static float HighlightMaskScaleForMode(int highlight_mode) {
    return 1.0f;
}

static float HighlightMaskAlphaCutoffForMode(int highlight_mode) {
    return highlight_mode == HIGHLIGHT_HOVER ? 0.26f : 0.24f;
}

static float HighlightOutlineAlphaCutoffForMode(int highlight_mode) {
    return HighlightMaskAlphaCutoffForMode(highlight_mode);
}

static float HighlightMaskExpandPxForMode(int highlight_mode) {
    return highlight_mode == HIGHLIGHT_HOVER ? 0.0f : 0.0f;
}

static float HighlightOutlineExpandPxForMode(int highlight_mode) {
    return highlight_mode == HIGHLIGHT_HOVER ? 8.0f : 6.0f;
}

static float HighlightOutlineMergePxForMode(int highlight_mode) {
    return highlight_mode == HIGHLIGHT_HOVER ? 6.0f : 5.0f;
}

static float HighlightOutlineGapPxForMode(int highlight_mode) {
    return highlight_mode == HIGHLIGHT_HOVER ? 10.0f : 8.0f;
}

static float HighlightOutlineThicknessPxForMode(int highlight_mode) {
    return highlight_mode == HIGHLIGHT_HOVER ? 6.0f : 5.0f;
}

static bool HasHighlightedObjects() {
    for (const SplatObject& object : g_splatObjects) {
        if (object.visible && object.highlight_mode != HIGHLIGHT_NONE && !object.local_splats.empty()) {
            return true;
        }
    }
    return false;
}

static void AppendQuadVertices(
    SplatVBOData* vbo_data,
    const float* center,
    const float* basis_x,
    const float* basis_y,
    const float* color) {
    if (!vbo_data) {
        return;
    }

    const GLuint base_index = static_cast<GLuint>(vbo_data->vertices.size());
    SplatVBOData::VertexData vertices[4] = {};

    const float quad_positions[4][3] = {
        {center[0] - basis_x[0] - basis_y[0], center[1] - basis_x[1] - basis_y[1], center[2] - basis_x[2] - basis_y[2]},
        {center[0] + basis_x[0] - basis_y[0], center[1] + basis_x[1] - basis_y[1], center[2] + basis_x[2] - basis_y[2]},
        {center[0] + basis_x[0] + basis_y[0], center[1] + basis_x[1] + basis_y[1], center[2] + basis_x[2] + basis_y[2]},
        {center[0] - basis_x[0] + basis_y[0], center[1] - basis_x[1] + basis_y[1], center[2] - basis_x[2] + basis_y[2]}
    };
    const float tex_coords[4][2] = {
        {0.0f, 0.0f},
        {1.0f, 0.0f},
        {1.0f, 1.0f},
        {0.0f, 1.0f}
    };

    for (int i = 0; i < 4; ++i) {
        vertices[i].position[0] = quad_positions[i][0];
        vertices[i].position[1] = quad_positions[i][1];
        vertices[i].position[2] = quad_positions[i][2];
        vertices[i].texCoord[0] = tex_coords[i][0];
        vertices[i].texCoord[1] = tex_coords[i][1];
        vertices[i].color[0] = color[0];
        vertices[i].color[1] = color[1];
        vertices[i].color[2] = color[2];
        vertices[i].color[3] = color[3];
        vbo_data->vertices.push_back(vertices[i]);
    }

    vbo_data->indices.push_back(base_index + 0);
    vbo_data->indices.push_back(base_index + 1);
    vbo_data->indices.push_back(base_index + 2);
    vbo_data->indices.push_back(base_index + 0);
    vbo_data->indices.push_back(base_index + 2);
    vbo_data->indices.push_back(base_index + 3);
}

static void UploadBufferData(SplatVBOData* vbo_data, GLenum usage) {
    if (!vbo_data || !vbo_data->initialized) {
        return;
    }

    glBindVertexArray(vbo_data->vao);
    glBindBuffer(GL_ARRAY_BUFFER, vbo_data->vbo);
    glBufferData(
        GL_ARRAY_BUFFER,
        static_cast<GLsizeiptr>(vbo_data->vertices.size() * sizeof(SplatVBOData::VertexData)),
        vbo_data->vertices.empty() ? nullptr : vbo_data->vertices.data(),
        usage);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, vbo_data->ebo);
    glBufferData(
        GL_ELEMENT_ARRAY_BUFFER,
        static_cast<GLsizeiptr>(vbo_data->indices.size() * sizeof(GLuint)),
        vbo_data->indices.empty() ? nullptr : vbo_data->indices.data(),
        usage);
    glBindVertexArray(0);
}

static GLuint CreateOutlineCompositeShader() {
    const char* vertexShaderSource = R"(#version 130
        in vec2 aClipPos;
        in vec2 aTexCoord;
        varying vec2 vTexCoord;
        void main() {
            gl_Position = vec4(aClipPos, 0.0, 1.0);
            vTexCoord = aTexCoord;
        })";
    const char* fragmentShaderSource = R"(#version 130
        uniform sampler2D uMaskTex;
        uniform vec2 uTexelSize;
        uniform float uThicknessPx;
        uniform float uMergePx;
        uniform float uGapPx;
        uniform float uInnerThreshold;
        uniform float uOuterThreshold;
        uniform vec4 uOutlineColor;
        varying vec2 vTexCoord;

        float sampleCoverageDisk(float radiusPx) {
            float weightedAlpha = texture2D(uMaskTex, vTexCoord).a * 2.0;
            float totalWeight = 2.0;
            const int kMaxRadius = 18;
            const int kDirectionCount = 40;
            for (int i = 1; i <= kMaxRadius; ++i) {
                if (float(i) > radiusPx) {
                    break;
                }
                float sampleRadius = float(i);
                float radialWeight = 1.0 - (sampleRadius / (radiusPx + 1.0));
                for (int dir = 0; dir < kDirectionCount; ++dir) {
                    float angle = 6.28318530718 * (float(dir) / float(kDirectionCount));
                    vec2 offset = vec2(cos(angle), sin(angle)) * uTexelSize * sampleRadius;
                    float sampleAlpha = texture2D(uMaskTex, vTexCoord + offset).a;
                    weightedAlpha += sampleAlpha * radialWeight;
                    totalWeight += radialWeight;
                }
            }
            return totalWeight > 0.0 ? (weightedAlpha / totalWeight) : 0.0;
        }

        void main() {
            float outerAlpha = sampleCoverageDisk(uMergePx + uGapPx + uThicknessPx);
            float innerAlpha = sampleCoverageDisk(uMergePx + uGapPx);

            if (outerAlpha < uOuterThreshold || innerAlpha >= uInnerThreshold) {
                discard;
            }

            gl_FragColor = vec4(uOutlineColor.rgb, uOutlineColor.a);
        })";

    GLuint vertexShader = glCreateShader(GL_VERTEX_SHADER);
    glShaderSource(vertexShader, 1, &vertexShaderSource, NULL);
    glCompileShader(vertexShader);
    GLint success = GL_FALSE;
    char infoLog[512] = {};
    glGetShaderiv(vertexShader, GL_COMPILE_STATUS, &success);
    if (!success) {
        glGetShaderInfoLog(vertexShader, 512, NULL, infoLog);
        LogRenderer("ERROR: Outline VS compile failed: %s", infoLog);
        glDeleteShader(vertexShader);
        return 0;
    }

    GLuint fragmentShader = glCreateShader(GL_FRAGMENT_SHADER);
    glShaderSource(fragmentShader, 1, &fragmentShaderSource, NULL);
    glCompileShader(fragmentShader);
    glGetShaderiv(fragmentShader, GL_COMPILE_STATUS, &success);
    if (!success) {
        glGetShaderInfoLog(fragmentShader, 512, NULL, infoLog);
        LogRenderer("ERROR: Outline FS compile failed: %s", infoLog);
        glDeleteShader(vertexShader);
        glDeleteShader(fragmentShader);
        return 0;
    }

    GLuint shaderProgram = glCreateProgram();
    glBindAttribLocation(shaderProgram, 0, "aClipPos");
    glBindAttribLocation(shaderProgram, 1, "aTexCoord");
    glAttachShader(shaderProgram, vertexShader);
    glAttachShader(shaderProgram, fragmentShader);
    glLinkProgram(shaderProgram);
    glGetProgramiv(shaderProgram, GL_LINK_STATUS, &success);
    if (!success) {
        glGetProgramInfoLog(shaderProgram, 512, NULL, infoLog);
        LogRenderer("ERROR: Outline shader link failed: %s", infoLog);
        glDeleteShader(vertexShader);
        glDeleteShader(fragmentShader);
        glDeleteProgram(shaderProgram);
        return 0;
    }

    glDeleteShader(vertexShader);
    glDeleteShader(fragmentShader);
    return shaderProgram;
}

static bool EnsureOutlineQuad() {
    if (g_outlineQuadVAO != 0 && g_outlineQuadVBO != 0) {
        return true;
    }

    const float quad_vertices[] = {
        -1.0f, -1.0f, 0.0f, 0.0f,
         1.0f, -1.0f, 1.0f, 0.0f,
        -1.0f,  1.0f, 0.0f, 1.0f,
         1.0f,  1.0f, 1.0f, 1.0f
    };

    glGenVertexArrays(1, &g_outlineQuadVAO);
    glGenBuffers(1, &g_outlineQuadVBO);
    if (g_outlineQuadVAO == 0 || g_outlineQuadVBO == 0) {
        LogRenderer("ERROR: Outline quad allocation failed.");
        return false;
    }

    glBindVertexArray(g_outlineQuadVAO);
    glBindBuffer(GL_ARRAY_BUFFER, g_outlineQuadVBO);
    glBufferData(GL_ARRAY_BUFFER, sizeof(quad_vertices), quad_vertices, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), reinterpret_cast<void*>(0));
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), reinterpret_cast<void*>(2 * sizeof(float)));
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    return true;
}

static bool EnsureOutlineTextures(int viewport_width, int viewport_height) {
    if (viewport_width <= 0 || viewport_height <= 0) {
        return false;
    }

    if (g_outlineCompositeShader == 0) {
        g_outlineCompositeShader = CreateOutlineCompositeShader();
        if (g_outlineCompositeShader == 0) {
            return false;
        }
    }

    if (g_outlineMaskFBO == 0) {
        glGenFramebuffers(1, &g_outlineMaskFBO);
    }
    if (g_outlineMaskColorTex == 0) {
        glGenTextures(1, &g_outlineMaskColorTex);
    }
    if (g_outlineMaskDepthTex == 0) {
        glGenTextures(1, &g_outlineMaskDepthTex);
    }
    if (g_sceneDepthTex == 0) {
        glGenTextures(1, &g_sceneDepthTex);
    }

    if (g_outlineViewportWidth != viewport_width || g_outlineViewportHeight != viewport_height) {
        g_outlineViewportWidth = viewport_width;
        g_outlineViewportHeight = viewport_height;

        glBindTexture(GL_TEXTURE_2D, g_outlineMaskColorTex);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, viewport_width, viewport_height, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);

        glBindTexture(GL_TEXTURE_2D, g_outlineMaskDepthTex);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_DEPTH_COMPONENT24, viewport_width, viewport_height, 0, GL_DEPTH_COMPONENT, GL_FLOAT, nullptr);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);

        glBindTexture(GL_TEXTURE_2D, g_sceneDepthTex);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_DEPTH_COMPONENT24, viewport_width, viewport_height, 0, GL_DEPTH_COMPONENT, GL_FLOAT, nullptr);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    }

    glBindFramebuffer(GL_FRAMEBUFFER, g_outlineMaskFBO);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, g_outlineMaskColorTex, 0);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_TEXTURE_2D, g_outlineMaskDepthTex, 0);
    GLenum status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    return status == GL_FRAMEBUFFER_COMPLETE;
}

static void UpdateHighlightVBOVertices(const NativeClipBoxState& clip_box_state) {
    if (!g_highlightMaskVBO.initialized || !g_outlineVBO.initialized) {
        return;
    }
    if (!g_highlightMaskVBO.needsUpdate && !g_outlineVBO.needsUpdate) {
        return;
    }
    if (!HasHighlightedObjects() && g_outlineVBO.vertices.empty() && g_highlightMaskVBO.vertices.empty()) {
        g_highlightMaskVBO.needsUpdate = false;
        g_outlineVBO.needsUpdate = false;
        return;
    }

    g_highlightMaskVBO.vertices.clear();
    g_highlightMaskVBO.indices.clear();
    g_outlineVBO.vertices.clear();
    g_outlineVBO.indices.clear();

    for (const SplatObject& object : g_splatObjects) {
        if (!object.visible || object.highlight_mode == HIGHLIGHT_NONE || object.local_splats.empty()) {
            continue;
        }

        const double sx = object.base_half_extents_xyz[0] > 1.0e-8 ? (object.half_extents_xyz[0] / object.base_half_extents_xyz[0]) : 1.0;
        const double sy = object.base_half_extents_xyz[1] > 1.0e-8 ? (object.half_extents_xyz[1] / object.base_half_extents_xyz[1]) : 1.0;
        const double sz = object.base_half_extents_xyz[2] > 1.0e-8 ? (object.half_extents_xyz[2] / object.base_half_extents_xyz[2]) : 1.0;

        float outline_color[4] = {};
        HighlightColorForMode(object.highlight_mode, &outline_color[0], &outline_color[1], &outline_color[2], &outline_color[3]);
        const float mask_scale = HighlightMaskScaleForMode(object.highlight_mode);
        const float outline_scale = HighlightScaleForMode(object.highlight_mode);

        for (const GaussSplat& local : object.local_splats) {
            const double local_x = static_cast<double>(local.position[0]) * sx;
            const double local_y = static_cast<double>(local.position[1]) * sy;
            const double local_z = static_cast<double>(local.position[2]) * sz;
            const float world_center[3] = {
                static_cast<float>(object.center_xyz[0] + (object.axes_xyz[0] * local_x) + (object.axes_xyz[3] * local_y) + (object.axes_xyz[6] * local_z)),
                static_cast<float>(object.center_xyz[1] + (object.axes_xyz[1] * local_x) + (object.axes_xyz[4] * local_y) + (object.axes_xyz[7] * local_z)),
                static_cast<float>(object.center_xyz[2] + (object.axes_xyz[2] * local_x) + (object.axes_xyz[5] * local_y) + (object.axes_xyz[8] * local_z))
            };
            if (!IsPointInsideClipBox(clip_box_state, world_center[0], world_center[1], world_center[2])) {
                continue;
            }

            float local_basis_x[3] = {};
            float local_basis_y[3] = {};
            float world_basis_x[3] = {};
            float world_basis_y[3] = {};
            float mask_basis_x[3] = {};
            float mask_basis_y[3] = {};
            ComputeBasisVectors(local, local_basis_x, local_basis_y);
            TransformVectorByObject(object, sx, sy, sz, local_basis_x, world_basis_x);
            TransformVectorByObject(object, sx, sy, sz, local_basis_y, world_basis_y);
            memcpy(mask_basis_x, world_basis_x, sizeof(mask_basis_x));
            memcpy(mask_basis_y, world_basis_y, sizeof(mask_basis_y));

            for (int axis = 0; axis < 3; ++axis) {
                mask_basis_x[axis] *= mask_scale;
                mask_basis_y[axis] *= mask_scale;
            }
            AppendQuadVertices(&g_highlightMaskVBO, world_center, mask_basis_x, mask_basis_y, outline_color);

            for (int axis = 0; axis < 3; ++axis) {
                world_basis_x[axis] *= outline_scale;
                world_basis_y[axis] *= outline_scale;
            }

            AppendQuadVertices(&g_outlineVBO, world_center, world_basis_x, world_basis_y, outline_color);
        }
    }

    if (g_highlightMaskVBO.vertices.empty()) {
        ClearUploadedBuffer(&g_highlightMaskVBO);
    }
    else {
        UploadBufferData(&g_highlightMaskVBO, GL_DYNAMIC_DRAW);
    }

    if (g_outlineVBO.vertices.empty()) {
        ClearUploadedBuffer(&g_outlineVBO);
    }
    else {
        UploadBufferData(&g_outlineVBO, GL_DYNAMIC_DRAW);
    }

    g_highlightMaskVBO.needsUpdate = false;
    g_outlineVBO.needsUpdate = false;
}

static void DrawSplatBuffer(
    const SplatVBOData& vbo_data,
    const float* mvp_matrix,
    float alpha_cutoff = 0.0f,
    float screen_expand_px = 0.0f,
    float viewport_width = 1.0f,
    float viewport_height = 1.0f,
    bool binary_mask = false) {
    if (!vbo_data.initialized || g_splatShader == 0 || vbo_data.ebo == 0 || vbo_data.indices.empty()) {
        return;
    }

    glUseProgram(g_splatShader);
    GLint mvpLoc = glGetUniformLocation(g_splatShader, "uMVP");
    if (mvpLoc != -1) {
        glUniformMatrix4fv(mvpLoc, 1, GL_TRUE, mvp_matrix);
    }
    GLint viewportLoc = glGetUniformLocation(g_splatShader, "uViewportSize");
    if (viewportLoc != -1) {
        glUniform2f(viewportLoc, viewport_width, viewport_height);
    }
    GLint expandLoc = glGetUniformLocation(g_splatShader, "uScreenExpandPx");
    if (expandLoc != -1) {
        glUniform1f(expandLoc, screen_expand_px);
    }

    GLint texLoc = glGetUniformLocation(g_splatShader, "uTexture");
    if (texLoc != -1 && g_gaussTexture != 0) {
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, g_gaussTexture);
        glUniform1i(texLoc, 0);
    }
    GLint alphaCutoffLoc = glGetUniformLocation(g_splatShader, "uAlphaCutoff");
    if (alphaCutoffLoc != -1) {
        glUniform1f(alphaCutoffLoc, alpha_cutoff);
    }
    GLint binaryMaskLoc = glGetUniformLocation(g_splatShader, "uBinaryMask");
    if (binaryMaskLoc != -1) {
        glUniform1i(binaryMaskLoc, binary_mask ? 1 : 0);
    }

    glBindVertexArray(vbo_data.vao);
    glDrawElements(GL_TRIANGLES, static_cast<GLsizei>(vbo_data.indices.size()), GL_UNSIGNED_INT, 0);
    glBindVertexArray(0);

    if (texLoc != -1) {
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, 0);
    }
}

static SplatObject* FindSplatObject(const char* object_id) {
    if (object_id == nullptr) {
        return nullptr;
    }

    for (SplatObject& object : g_splatObjects) {
        if (object.id == object_id) {
            return &object;
        }
    }

    return nullptr;
}

static void ResetSplatObjects() {
    g_splatObjects.clear();
    g_splats.clear();
    g_splatSortIndices.clear();
    g_splatSortCache.clear();
    MarkSplatBuffersDirty();
}

static void RefreshWorldSplatsFromObjects() {
    g_splats.clear();

    for (const SplatObject& object : g_splatObjects) {
        if (!object.visible) {
            continue;
        }

        const double sx = object.base_half_extents_xyz[0] > 1.0e-8 ? (object.half_extents_xyz[0] / object.base_half_extents_xyz[0]) : 1.0;
        const double sy = object.base_half_extents_xyz[1] > 1.0e-8 ? (object.half_extents_xyz[1] / object.base_half_extents_xyz[1]) : 1.0;
        const double sz = object.base_half_extents_xyz[2] > 1.0e-8 ? (object.half_extents_xyz[2] / object.base_half_extents_xyz[2]) : 1.0;
        for (const GaussSplat& local : object.local_splats) {
            GaussSplat world = local;
            const double local_x = static_cast<double>(local.position[0]) * sx;
            const double local_y = static_cast<double>(local.position[1]) * sy;
            const double local_z = static_cast<double>(local.position[2]) * sz;
            world.position[0] = static_cast<float>(object.center_xyz[0] + (object.axes_xyz[0] * local_x) + (object.axes_xyz[3] * local_y) + (object.axes_xyz[6] * local_z));
            world.position[1] = static_cast<float>(object.center_xyz[1] + (object.axes_xyz[1] * local_x) + (object.axes_xyz[4] * local_y) + (object.axes_xyz[7] * local_z));
            world.position[2] = static_cast<float>(object.center_xyz[2] + (object.axes_xyz[2] * local_x) + (object.axes_xyz[5] * local_y) + (object.axes_xyz[8] * local_z));
            float local_basis_x[3] = {};
            float local_basis_y[3] = {};
            ComputeBasisVectors(local, local_basis_x, local_basis_y);
            TransformVectorByObject(object, sx, sy, sz, local_basis_x, world.basis_x);
            TransformVectorByObject(object, sx, sy, sz, local_basis_y, world.basis_y);
            world.use_custom_basis = 1;
            world.highlight_mode = object.highlight_mode;
            g_splats.push_back(world);
        }
    }

    g_splatSortIndices.clear();
    g_splatSortCache.clear();
    MarkSplatBuffersDirty();
}

static GLuint CreateSplatShader() {
    LogRenderer("Creating splat shader...");
    const char* vertexShaderSource = R"(#version 130
        attribute vec3 aPos; attribute vec2 aTexCoord; attribute vec4 aColor;
        uniform mat4 uMVP; uniform vec2 uViewportSize; uniform float uScreenExpandPx;
        varying vec2 vTexCoord; varying vec4 vColor;
        void main() {
            vec4 clip = uMVP * vec4(aPos, 1.0);
            if (uScreenExpandPx > 0.0 && uViewportSize.x > 0.0 && uViewportSize.y > 0.0) {
                vec2 corner = (aTexCoord * 2.0) - 1.0;
                vec2 ndcOffset = vec2((uScreenExpandPx * 2.0) / uViewportSize.x, (uScreenExpandPx * 2.0) / uViewportSize.y);
                clip.xy += corner * ndcOffset * clip.w;
            }
            gl_Position = clip; vTexCoord = aTexCoord; vColor = aColor;
        })";
    const char* fragmentShaderSource = R"(#version 130
        varying vec2 vTexCoord; varying vec4 vColor; uniform sampler2D uTexture; uniform float uAlphaCutoff; uniform int uBinaryMask;
        void main() {
            vec4 texColor = texture2D(uTexture, vTexCoord);
            float alpha = texColor.a * vColor.a;
            if (alpha <= uAlphaCutoff) discard;
            gl_FragColor = vec4(vColor.rgb, uBinaryMask != 0 ? 1.0 : alpha);
        })";

    GLuint vertexShader = glCreateShader(GL_VERTEX_SHADER);
    glShaderSource(vertexShader, 1, &vertexShaderSource, NULL);
    glCompileShader(vertexShader);
    GLint success; char infoLog[512]; glGetShaderiv(vertexShader, GL_COMPILE_STATUS, &success);
    if (!success) { glGetShaderInfoLog(vertexShader, 512, NULL, infoLog); LogRenderer("ERROR: VS compile failed: %s", infoLog); glDeleteShader(vertexShader); return 0; }

    GLuint fragmentShader = glCreateShader(GL_FRAGMENT_SHADER);
    glShaderSource(fragmentShader, 1, &fragmentShaderSource, NULL);
    glCompileShader(fragmentShader);
    glGetShaderiv(fragmentShader, GL_COMPILE_STATUS, &success);
    if (!success) { glGetShaderInfoLog(fragmentShader, 512, NULL, infoLog); LogRenderer("ERROR: FS compile failed: %s", infoLog); glDeleteShader(vertexShader); glDeleteShader(fragmentShader); return 0; }

    GLuint shaderProgram = glCreateProgram();
    glAttachShader(shaderProgram, vertexShader);
    glAttachShader(shaderProgram, fragmentShader);
    glBindAttribLocation(shaderProgram, 0, "aPos");
    glBindAttribLocation(shaderProgram, 1, "aTexCoord");
    glBindAttribLocation(shaderProgram, 2, "aColor");
    glLinkProgram(shaderProgram);
    glGetProgramiv(shaderProgram, GL_LINK_STATUS, &success);
    if (!success) { glGetProgramInfoLog(shaderProgram, 512, NULL, infoLog); LogRenderer("ERROR: Shader link failed: %s", infoLog); }

    glDeleteShader(vertexShader); glDeleteShader(fragmentShader);
    if (!success) { glDeleteProgram(shaderProgram); return 0; }
    LogRenderer("Shader program created successfully (ID: %u).", shaderProgram);
    return shaderProgram;
}

static GLuint CreateGaussianTexture(int size, float sigma) {
    LogRenderer("Creating gaussian texture %dx%d sigma=%.2f", size, size, sigma);
    unsigned char* data = new unsigned char[size * size * 4]; float center = size / 2.0f;
    for (int y = 0; y < size; y++) for (int x = 0; x < size; x++) {
        float dx = (x - center + 0.5f) / center; float dy = (y - center + 0.5f) / center;
        float distSq = dx * dx + dy * dy; float alpha = exp(-distSq / (2.0f * sigma * sigma));
        data[(y * size + x) * 4 + 0] = 255; data[(y * size + x) * 4 + 1] = 255; data[(y * size + x) * 4 + 2] = 255; data[(y * size + x) * 4 + 3] = (unsigned char)(alpha * 255.0f);
    }
    GLuint texture = 0; glGenTextures(1, &texture); if (texture == 0) { LogRenderer("ERROR glGenTextures failed"); delete[] data; return 0; }
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, size, size, 0, GL_RGBA, GL_UNSIGNED_BYTE, data);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glBindTexture(GL_TEXTURE_2D, 0); delete[] data;
    LogRenderer("Gaussian texture created (ID: %u)", texture);
    return texture;
}

static void InitializeSplatVBO() {
    if (g_splatVBO.initialized) return;
    LogRenderer("Initializing Splat VBO...");
    GLenum err = glewInit(); if (err != GLEW_OK) { LogRenderer("ERROR: GLEW init failed: %s", glewGetErrorString(err)); return; }
    if (!GLEW_VERSION_2_0 || !GLEW_ARB_vertex_buffer_object) { LogRenderer("WARN: OpenGL VBO/Shader support not available."); return; }

    if (g_splatShader == 0) { g_splatShader = CreateSplatShader(); }
    if (g_splatShader == 0) { LogRenderer("ERROR: Shader creation failed during VBO init. VBO unusable."); return; }

    if (!InitializeBufferObjects(&g_splatVBO)) {
        LogRenderer("ERROR: Failed to initialize primary splat VBO.");
        return;
    }
    if (!InitializeBufferObjects(&g_highlightMaskVBO)) {
        LogRenderer("ERROR: Failed to initialize highlight mask splat VBO.");
        return;
    }
    if (!InitializeBufferObjects(&g_outlineVBO)) {
        LogRenderer("ERROR: Failed to initialize outline splat VBO.");
        return;
    }

    LogRenderer("Splat VBO initialized successfully (main VAO:%u, mask VAO:%u, outline VAO:%u).", g_splatVBO.vao, g_highlightMaskVBO.vao, g_outlineVBO.vao);
}

static void UpdateSplatVBOVertices() {
    if (!g_splatVBO.initialized) { LogRenderer("DEBUG: UpdateVBOVertices skip - not inited"); return; }
    if (!g_splatVBO.needsUpdate) { return; }
    if (g_splats.empty() && g_splatVBO.vertices.empty()) { g_splatVBO.needsUpdate = false; return; }

    LogRenderer("DEBUG: Entering UpdateSplatVBOVertices for %zu splats...", g_splats.size());

    if (g_splats.empty()) {
        ClearUploadedBuffer(&g_splatVBO);
        g_splatSortIndices.clear();
        LogRenderer("DEBUG: Cleared VBO/EBO/Indices as splats are empty.");
    }
    else {
        g_splatVBO.vertices.clear();
        g_splatVBO.vertices.reserve(g_splats.size() * 4);
        float quadSize = 1.0f;
        for (size_t i = 0; i < g_splats.size(); ++i) {
            const GaussSplat& splat = g_splats[i];
            float rotatedXVec[3] = {};
            float rotatedYVec[3] = {};
            ComputeBasisVectors(splat, rotatedXVec, rotatedYVec);
            rotatedXVec[0] *= quadSize; rotatedXVec[1] *= quadSize; rotatedXVec[2] *= quadSize;
            rotatedYVec[0] *= quadSize; rotatedYVec[1] *= quadSize; rotatedYVec[2] *= quadSize;
            SplatVBOData::VertexData v0, v1, v2, v3;
            v0.position[0] = splat.position[0] - rotatedXVec[0] - rotatedYVec[0]; v0.position[1] = splat.position[1] - rotatedXVec[1] - rotatedYVec[1]; v0.position[2] = splat.position[2] - rotatedXVec[2] - rotatedYVec[2]; v0.texCoord[0] = 0.0f; v0.texCoord[1] = 0.0f;
            v1.position[0] = splat.position[0] + rotatedXVec[0] - rotatedYVec[0]; v1.position[1] = splat.position[1] + rotatedXVec[1] - rotatedYVec[1]; v1.position[2] = splat.position[2] + rotatedXVec[2] - rotatedYVec[2]; v1.texCoord[0] = 1.0f; v1.texCoord[1] = 0.0f;
            v2.position[0] = splat.position[0] + rotatedXVec[0] + rotatedYVec[0]; v2.position[1] = splat.position[1] + rotatedXVec[1] + rotatedYVec[1]; v2.position[2] = splat.position[2] + rotatedXVec[2] + rotatedYVec[2]; v2.texCoord[0] = 1.0f; v2.texCoord[1] = 1.0f;
            v3.position[0] = splat.position[0] - rotatedXVec[0] + rotatedYVec[0]; v3.position[1] = splat.position[1] - rotatedXVec[1] + rotatedYVec[1]; v3.position[2] = splat.position[2] - rotatedXVec[2] + rotatedYVec[2]; v3.texCoord[0] = 0.0f; v3.texCoord[1] = 1.0f;
            v0.color[0] = v1.color[0] = v2.color[0] = v3.color[0] = splat.color[0]; v0.color[1] = v1.color[1] = v2.color[1] = v3.color[1] = splat.color[1]; v0.color[2] = v1.color[2] = v2.color[2] = v3.color[2] = splat.color[2]; v0.color[3] = v1.color[3] = v2.color[3] = v3.color[3] = splat.color[3];
            g_splatVBO.vertices.push_back(v0); g_splatVBO.vertices.push_back(v1); g_splatVBO.vertices.push_back(v2); g_splatVBO.vertices.push_back(v3);
        }
        UploadBufferData(&g_splatVBO, GL_STATIC_DRAW);

        g_splatSortIndices.resize(g_splats.size());
        std::iota(g_splatSortIndices.begin(), g_splatSortIndices.end(), 0);
        LogRenderer("DEBUG: Initialized g_splatSortIndices with size %zu after VBO update", g_splatSortIndices.size());
    }
    g_splatVBO.needsUpdate = false;
    LogRenderer("DEBUG: Exiting UpdateSplatVBOVertices. Vertex count: %zu", g_splatVBO.vertices.size());
}

static void UpdateSplatEBO() {
    if (!g_splatVBO.initialized) {
        return;
    }
    if (g_splatVBO.ebo == 0) {
        LogRenderer("ERROR: UpdateSplatEBO - EBO ID is 0!");
        return;
    }

    if (g_splatSortIndices.empty()) {
        if (!g_splatVBO.indices.empty()) {
            LogRenderer("DEBUG: Clearing EBO as sort indices are empty.");
            glBindVertexArray(g_splatVBO.vao);
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo);
            glBufferData(GL_ELEMENT_ARRAY_BUFFER, 0, nullptr, GL_DYNAMIC_DRAW);
            glBindVertexArray(0);
            g_splatVBO.indices.clear();
        }
        return;
    }


    g_splatVBO.indices.clear();
    g_splatVBO.indices.reserve(g_splatSortIndices.size() * 6);

    // Rebuild draw indices from the current view-dependent splat order.
    for (GLuint splatIndex : g_splatSortIndices) {
        if (splatIndex >= g_splats.size()) {
            continue;
        }

        GLuint baseVertexIndex = splatIndex * 4;

        if (baseVertexIndex + 3 >= g_splatVBO.vertices.size()) {
            continue;
        }

        g_splatVBO.indices.push_back(baseVertexIndex + 0);
        g_splatVBO.indices.push_back(baseVertexIndex + 1);
        g_splatVBO.indices.push_back(baseVertexIndex + 2);

        g_splatVBO.indices.push_back(baseVertexIndex + 0);
        g_splatVBO.indices.push_back(baseVertexIndex + 2);
        g_splatVBO.indices.push_back(baseVertexIndex + 3);
    }


    glBindVertexArray(g_splatVBO.vao);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo);

    glBufferData(GL_ELEMENT_ARRAY_BUFFER,
        g_splatVBO.indices.size() * sizeof(GLuint),
        g_splatVBO.indices.data(),
        GL_DYNAMIC_DRAW);

    glBindVertexArray(0);

    GLenum err = glGetError();
    if (err != GL_NO_ERROR) {
        LogRenderer("ERROR: OpenGL Error 0x%x after EBO update (glBufferData)! Check buffer size and memory.", err);
    }
}

static void InitializeDefaultSplats() { LogRenderer("Default splat disabled."); g_splats.clear(); g_splatSortIndices.clear(); MarkSplatBuffersDirty(); }
static void EnsureTextureInitialized() { if (!g_textureInitialized) { g_gaussTexture = CreateGaussianTexture(64, 0.3f); g_textureInitialized = true; if (g_gaussTexture == 0) LogRenderer("ERR: Gauss texture failed."); } }
static void LoadHookFunctions() {
    if (GetMatrixByLocation && GetCameraState && GetClipBoxState) return;
    HMODULE dll = GetModuleHandleA("SketchUpOverlayBridge.dll");
    if (!dll) {
        LogRenderer("ERR: Overlay bridge DLL not found.");
        return;
    }
    if (!GetMatrixByLocation) {
        GetMatrixByLocation = (PFN_GET_MATRIX_BY_LOC)GetProcAddress(dll, "GetMatrixByLocation");
        if (GetMatrixByLocation) LogRenderer("Found GetMatrixByLocation."); else LogRenderer("ERR: GetMatrixByLocation not found.");
    }
    if (!GetCameraState) {
        GetCameraState = (PFN_GET_CAMERA_STATE)GetProcAddress(dll, "GetCameraState");
        if (GetCameraState) LogRenderer("Found GetCameraState."); else LogRenderer("ERR: GetCameraState not found.");
    }
    if (!GetClipBoxState) {
        GetClipBoxState = (PFN_GET_CLIP_BOX_STATE)GetProcAddress(dll, "GetClipBoxState");
        if (GetClipBoxState) LogRenderer("Found GetClipBoxState."); else LogRenderer("WARN: GetClipBoxState not found.");
    }
}
static void Normalize3(float* vector) {
    float length = sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]);
    if (length > 1e-5f) {
        vector[0] /= length;
        vector[1] /= length;
        vector[2] /= length;
    }
}
static void CrossProduct(const float* v1, const float* v2, float* r) { r[0] = v1[1] * v2[2] - v1[2] * v2[1]; r[1] = v1[2] * v2[0] - v1[0] * v2[2]; r[2] = v1[0] * v2[1] - v1[1] * v2[0]; }

extern "C" EXPORT void SetPointCloud(const double* points_in, int count) { LogRenderer("SetPointCloud called."); if (!points_in || count <= 0)return; LoadHookFunctions(); g_points.clear(); g_points.reserve(count * 6); for (int i = 0;i < count;++i) { g_points.push_back((float)points_in[i * 6 + 0]); g_points.push_back((float)points_in[i * 6 + 1]); g_points.push_back((float)points_in[i * 6 + 2]); g_points.push_back((float)points_in[i * 6 + 3] / 255.f); g_points.push_back((float)points_in[i * 6 + 4] / 255.f); g_points.push_back((float)points_in[i * 6 + 5] / 255.f); } g_dataReady = true; }
extern "C" EXPORT void AddSplat(float x, float y, float z, float r, float g, float b, float a, float scaleX, float scaleY, float rotation, bool rotateVertical) { GaussSplat s = {}; s.position[0] = x;s.position[1] = y;s.position[2] = z; s.color[0] = r;s.color[1] = g;s.color[2] = b;s.color[3] = a; s.scale[0] = scaleX;s.scale[1] = scaleY; float an = rotation * M_PI / 180.f, ha = an * .5f, sn = sin(ha), cn = cos(ha); s.rotation[0] = cn; if (rotateVertical) { s.rotation[1] = sn;s.rotation[2] = 0;s.rotation[3] = 0; } else { s.rotation[1] = 0;s.rotation[2] = 0;s.rotation[3] = sn; } s.highlight_mode = HIGHLIGHT_NONE; g_splats.push_back(s); MarkSplatBuffersDirty(); }
extern "C" EXPORT void AddSplatWithQuaternion(float x, float y, float z, float r, float g, float b, float a, float scaleX, float scaleY, float qw, float qx, float qy, float qz) { GaussSplat s = {}; s.position[0] = x;s.position[1] = y;s.position[2] = z; s.color[0] = r;s.color[1] = g;s.color[2] = b;s.color[3] = std::max(0.01f, std::min(a, 1.f)); s.scale[0] = scaleX;s.scale[1] = scaleY; float n = sqrt(qw * qw + qx * qx + qy * qy + qz * qz); if (n > 1e-5f) { s.rotation[0] = qw / n;s.rotation[1] = qx / n;s.rotation[2] = qy / n;s.rotation[3] = qz / n; } else { s.rotation[0] = 1;s.rotation[1] = 0;s.rotation[2] = 0;s.rotation[3] = 0; } s.highlight_mode = HIGHLIGHT_NONE; g_splats.push_back(s); MarkSplatBuffersDirty(); }
extern "C" EXPORT void ClearSplats() { LogRenderer("ClearSplats called."); ResetSplatObjects(); }
extern "C" EXPORT void ClearSplatObjects() { ResetSplatObjects(); }
extern "C" EXPORT void SetSplatSortingMode(SplatSortingMode mode) { if (mode >= 0 && mode <= 5) { g_sortingMode = mode; LogRenderer("Sort mode set %d.", mode); } else LogRenderer("Invalid sort mode %d.", mode); }

static void RenderSingleSplatIM(const GaussSplat& splat, const float* viewMatrix, const float* projectionMatrix) { float basis_x[3] = {}; float basis_y[3] = {}; ComputeBasisVectors(splat, basis_x, basis_y); glColor4f(splat.color[0], splat.color[1], splat.color[2], splat.color[3]); glBegin(GL_QUADS); glTexCoord2f(0, 0);glVertex3f(splat.position[0] - basis_x[0] - basis_y[0], splat.position[1] - basis_x[1] - basis_y[1], splat.position[2] - basis_x[2] - basis_y[2]); glTexCoord2f(1, 0);glVertex3f(splat.position[0] + basis_x[0] - basis_y[0], splat.position[1] + basis_x[1] - basis_y[1], splat.position[2] + basis_x[2] - basis_y[2]); glTexCoord2f(1, 1);glVertex3f(splat.position[0] + basis_x[0] + basis_y[0], splat.position[1] + basis_x[1] + basis_y[1], splat.position[2] + basis_x[2] + basis_y[2]); glTexCoord2f(0, 1);glVertex3f(splat.position[0] - basis_x[0] + basis_y[0], splat.position[1] - basis_x[1] + basis_y[1], splat.position[2] - basis_x[2] + basis_y[2]); glEnd(); }
static void RenderSingleSplatIMWithBasis(const float* center, const float* basis_x, const float* basis_y, const float* color) { glColor4f(color[0], color[1], color[2], color[3]); glBegin(GL_QUADS); glTexCoord2f(0, 0);glVertex3f(center[0] - basis_x[0] - basis_y[0], center[1] - basis_x[1] - basis_y[1], center[2] - basis_x[2] - basis_y[2]); glTexCoord2f(1, 0);glVertex3f(center[0] + basis_x[0] - basis_y[0], center[1] + basis_x[1] - basis_y[1], center[2] + basis_x[2] - basis_y[2]); glTexCoord2f(1, 1);glVertex3f(center[0] + basis_x[0] + basis_y[0], center[1] + basis_x[1] + basis_y[1], center[2] + basis_x[2] + basis_y[2]); glTexCoord2f(0, 1);glVertex3f(center[0] - basis_x[0] + basis_y[0], center[1] - basis_x[1] + basis_y[1], center[2] - basis_x[2] + basis_y[2]); glEnd(); }
static void RenderHighlightedSplatsIM(const NativeClipBoxState& clip_box_state) {
    for (const SplatObject& object : g_splatObjects) {
        if (!object.visible || object.highlight_mode == HIGHLIGHT_NONE || object.local_splats.empty()) {
            continue;
        }

        const double sx = object.base_half_extents_xyz[0] > 1.0e-8 ? (object.half_extents_xyz[0] / object.base_half_extents_xyz[0]) : 1.0;
        const double sy = object.base_half_extents_xyz[1] > 1.0e-8 ? (object.half_extents_xyz[1] / object.base_half_extents_xyz[1]) : 1.0;
        const double sz = object.base_half_extents_xyz[2] > 1.0e-8 ? (object.half_extents_xyz[2] / object.base_half_extents_xyz[2]) : 1.0;
        float outline_color[4] = {};
        HighlightColorForMode(object.highlight_mode, &outline_color[0], &outline_color[1], &outline_color[2], &outline_color[3]);
        const float outline_scale = HighlightScaleForMode(object.highlight_mode);

        for (const GaussSplat& local : object.local_splats) {
            const double local_x = static_cast<double>(local.position[0]) * sx;
            const double local_y = static_cast<double>(local.position[1]) * sy;
            const double local_z = static_cast<double>(local.position[2]) * sz;
            const float world_center[3] = {
                static_cast<float>(object.center_xyz[0] + (object.axes_xyz[0] * local_x) + (object.axes_xyz[3] * local_y) + (object.axes_xyz[6] * local_z)),
                static_cast<float>(object.center_xyz[1] + (object.axes_xyz[1] * local_x) + (object.axes_xyz[4] * local_y) + (object.axes_xyz[7] * local_z)),
                static_cast<float>(object.center_xyz[2] + (object.axes_xyz[2] * local_x) + (object.axes_xyz[5] * local_y) + (object.axes_xyz[8] * local_z))
            };
            if (!IsPointInsideClipBox(clip_box_state, world_center[0], world_center[1], world_center[2])) {
                continue;
            }

            float local_basis_x[3] = {};
            float local_basis_y[3] = {};
            float world_basis_x[3] = {};
            float world_basis_y[3] = {};
            ComputeBasisVectors(local, local_basis_x, local_basis_y);
            TransformVectorByObject(object, sx, sy, sz, local_basis_x, world_basis_x);
            TransformVectorByObject(object, sx, sy, sz, local_basis_y, world_basis_y);
            for (int axis = 0; axis < 3; ++axis) {
                world_basis_x[axis] *= outline_scale;
                world_basis_y[axis] *= outline_scale;
            }

            RenderSingleSplatIMWithBasis(world_center, world_basis_x, world_basis_y, outline_color);
        }
    }
}
extern "C" EXPORT int GetSplatBounds(double* out_min_xyz, double* out_max_xyz) {
    // Bounds are consumed by the SketchUp-side proxy to keep clip planes stable.
    if (!out_min_xyz || !out_max_xyz || g_splats.empty()) {
        return 0;
    }

    double min_x = g_splats[0].position[0];
    double min_y = g_splats[0].position[1];
    double min_z = g_splats[0].position[2];
    double max_x = min_x;
    double max_y = min_y;
    double max_z = min_z;

    for (const GaussSplat& splat : g_splats) {
        min_x = std::min(min_x, static_cast<double>(splat.position[0]));
        min_y = std::min(min_y, static_cast<double>(splat.position[1]));
        min_z = std::min(min_z, static_cast<double>(splat.position[2]));
        max_x = std::max(max_x, static_cast<double>(splat.position[0]));
        max_y = std::max(max_y, static_cast<double>(splat.position[1]));
        max_z = std::max(max_z, static_cast<double>(splat.position[2]));
    }

    out_min_xyz[0] = min_x;
    out_min_xyz[1] = min_y;
    out_min_xyz[2] = min_z;
    out_max_xyz[0] = max_x;
    out_max_xyz[1] = max_y;
    out_max_xyz[2] = max_z;
    return 1;
}
static bool CheckGLCapabilities() { GLenum e = glewInit(); if (e != GLEW_OK) { LogRenderer("GLEW failed:%s", glewGetErrorString(e));return false; } if (!GLEW_VERSION_2_0 || !GLEW_ARB_vertex_buffer_object) { LogRenderer("WARN No VBO/Shader support");return false; } LogRenderer("OpenGL VBO/Shader support detected."); return true; }

extern "C" EXPORT void renderPointCloud() {
    LoadHookFunctions(); if (!GetMatrixByLocation) { LogRenderer("ERROR: GetMatrixByLocation is NULL, cannot proceed."); return; } if (!GetCameraState) { LogRenderer("ERROR: GetCameraState is NULL, cannot proceed."); return; }

    static bool firstCall = true; static bool useVBO = false;
    if (firstCall) {
        LogRenderer("DEBUG: First call to renderPointCloud.");
        EnsureTextureInitialized();
        useVBO = CheckGLCapabilities();
        if (useVBO) InitializeSplatVBO();
        // Do not inject a synthetic test splat on startup.
        firstCall = false;
    }
    if (useVBO && !g_splatVBO.initialized) {
        LogRenderer("DEBUG: Attempting VBO re-initialization...");
        InitializeSplatVBO();
        if (!g_splatVBO.initialized) { LogRenderer("WARN: VBO re-init failed. Disabling VBO path."); useVBO = false; }
    }

    if (g_splats.empty()) { return; }

    if (g_splatVBO.needsUpdate) {
        LogRenderer("DEBUG: renderPointCloud - needsUpdate=true, calling UpdateSplatVBOVertices()");
        UpdateSplatVBOVertices();
        if (!g_splats.empty()) { LogRenderer("DEBUG: renderPointCloud - VBO updated, calling UpdateSplatEBO() for initial order."); UpdateSplatEBO(); }
    }

    float viewMatrix[16] = { 0 };
    float mvpMatrix[16] = { 0 };
    float projectionMatrix[16] = { 0 };
    float camPos[3] = { 0 };
    float camTarget[3] = { 0 };
    float camUp[3] = { 0, 1, 0 };
    float viewDir[3] = { 0, 0, -1 };
    int isPerspective = 1;
    int viewLoc = 14;
    int projLoc = 15;
    bool hasView = GetMatrixByLocation(viewLoc, viewMatrix);
    bool hasProj = GetMatrixByLocation(projLoc, projectionMatrix);
    bool hasCamera = GetCameraState(camPos, camTarget, camUp, &isPerspective);

    if (!hasView || !hasProj) {
        LogRenderer("WARN: Missing View(loc%d)=%d or Projection(loc%d)=%d matrix. Skipping render.", viewLoc, hasView, projLoc, hasProj);
        return;
    }
    if (!hasCamera) {
        LogRenderer("WARN: Camera state unavailable. Skipping render.");
        return;
    }

    viewDir[0] = camTarget[0] - camPos[0];
    viewDir[1] = camTarget[1] - camPos[1];
    viewDir[2] = camTarget[2] - camPos[2];
    Normalize3(viewDir);
    Normalize3(camUp);

    MultiplyMat4(projectionMatrix, viewMatrix, mvpMatrix);
    const NativeClipBoxState clipBoxState = FetchClipBoxStateSnapshot();
    const bool clipStateChanged = !g_hasLastClipBoxState || !ClipStatesEqual(clipBoxState, g_lastClipBoxState);
    if (clipStateChanged) {
        g_lastClipBoxState = clipBoxState;
        g_hasLastClipBoxState = true;
        g_outlineVBO.needsUpdate = true;
    }

    if (useVBO && g_highlightMaskVBO.initialized && g_outlineVBO.initialized &&
        (g_highlightMaskVBO.needsUpdate || g_outlineVBO.needsUpdate)) {
        UpdateHighlightVBOVertices(clipBoxState);
    }

    bool sorting_done = false;
    if (hasCamera) {
        bool needSorting = false;

        float posDistSq =
            (camPos[0] - g_lastCamPos[0]) * (camPos[0] - g_lastCamPos[0]) +
            (camPos[1] - g_lastCamPos[1]) * (camPos[1] - g_lastCamPos[1]) +
            (camPos[2] - g_lastCamPos[2]) * (camPos[2] - g_lastCamPos[2]);

        float dirDiff =
            (viewDir[0] - g_lastViewDir[0]) * (viewDir[0] - g_lastViewDir[0]) +
            (viewDir[1] - g_lastViewDir[1]) * (viewDir[1] - g_lastViewDir[1]) +
            (viewDir[2] - g_lastViewDir[2]) * (viewDir[2] - g_lastViewDir[2]);

        g_framesSinceLastSort++;

        if (clipStateChanged || posDistSq > 0.0001f || dirDiff > 0.000001f || g_framesSinceLastSort >= SORT_EVERY_N_FRAMES) {
            needSorting = true;

            memcpy(g_lastCamPos, camPos, 3 * sizeof(float));
            memcpy(g_lastViewDir, viewDir, 3 * sizeof(float));

            g_framesSinceLastSort = 0;
        }

        if (needSorting) {
            LogRenderer("DEBUG: Sorting %zu indices for view changes or frame limit...", g_splats.size());

            g_splatSortCache.clear();
            g_splatSortCache.reserve(g_splats.size());

            // Cache the current camera-relative ordering so alpha blending stays stable.
            for (size_t i = 0; i < g_splats.size(); ++i) {
                const GaussSplat& splat = g_splats[i];
                if (!IsPointInsideClipBox(clipBoxState, splat.position[0], splat.position[1], splat.position[2])) {
                    continue;
                }

                SplatSortData sortData = {};

                sortData.index = i;

                float vec[3] = {
                    splat.position[0] - camPos[0],
                    splat.position[1] - camPos[1],
                    splat.position[2] - camPos[2]
                };

                sortData.projValue = vec[0] * viewDir[0] + vec[1] * viewDir[1] + vec[2] * viewDir[2];

                sortData.distanceSquared = vec[0] * vec[0] + vec[1] * vec[1] + vec[2] * vec[2];

                sortData.isBackfacing = (sortData.projValue <= 0);

                if (!sortData.isBackfacing) {
                    float distanceFactor = sortData.distanceSquared * 0.001f;
                    if (sortData.projValue > 0.001f) {
                        distanceFactor /= sortData.projValue;
                    }
                    sortData.sortKey = sortData.projValue + distanceFactor;
                }
                else {
                    sortData.sortKey = sortData.distanceSquared;
                }

                g_splatSortCache.push_back(sortData);
            }

            std::sort(g_splatSortCache.begin(), g_splatSortCache.end(),
                [](const SplatSortData& a, const SplatSortData& b) -> bool {
                    if (a.isBackfacing != b.isBackfacing) return a.isBackfacing;

                    if (a.isBackfacing) return a.distanceSquared > b.distanceSquared;

                    return a.sortKey > b.sortKey;
                });

            g_splatSortIndices.resize(g_splatSortCache.size());
            for (size_t i = 0; i < g_splatSortCache.size(); ++i) {
                g_splatSortIndices[i] = g_splatSortCache[i].index;
            }

            sorting_done = true;
        }
    }
    else {
        LogRenderer("DEBUG: Skipping sorting (no view matrix).");
        if (g_splatVBO.indices.size() != g_splatSortIndices.size() * 6) { LogRenderer("DEBUG: EBO needs update (default order)."); UpdateSplatEBO(); }
    }

    if (sorting_done) { /* LogRenderer("DEBUG: Calling UpdateSplatEBO() after sort.");*/ UpdateSplatEBO(); }

    GLint oldProg = 0; glGetIntegerv(GL_CURRENT_PROGRAM, &oldProg); GLboolean blendEn = glIsEnabled(GL_BLEND); GLint oldBlendSrcRGB, oldBlendDstRGB, oldBlendSrcAlpha, oldBlendDstAlpha; glGetIntegerv(GL_BLEND_SRC_RGB, &oldBlendSrcRGB); glGetIntegerv(GL_BLEND_DST_RGB, &oldBlendDstRGB); glGetIntegerv(GL_BLEND_SRC_ALPHA, &oldBlendSrcAlpha); glGetIntegerv(GL_BLEND_DST_ALPHA, &oldBlendDstAlpha); GLboolean depthEn = glIsEnabled(GL_DEPTH_TEST); GLboolean depthMask; glGetBooleanv(GL_DEPTH_WRITEMASK, &depthMask); GLint oldDepthFunc; glGetIntegerv(GL_DEPTH_FUNC, &oldDepthFunc); GLboolean cullEn = glIsEnabled(GL_CULL_FACE); GLint oldCullMode; glGetIntegerv(GL_CULL_FACE_MODE, &oldCullMode); GLboolean texEn = glIsEnabled(GL_TEXTURE_2D); GLint oldActiveTex = 0; glGetIntegerv(GL_ACTIVE_TEXTURE, &oldActiveTex); GLint oldTexBind = 0; glGetIntegerv(GL_TEXTURE_BINDING_2D, &oldTexBind); GLint stencilBits = 0; glGetIntegerv(GL_STENCIL_BITS, &stencilBits); GLboolean stencilEn = glIsEnabled(GL_STENCIL_TEST); GLint oldStencilFunc = GL_ALWAYS; GLint oldStencilRef = 0; GLint oldStencilValueMask = ~0; GLint oldStencilWriteMask = ~0; GLint oldStencilFail = GL_KEEP; GLint oldStencilZFail = GL_KEEP; GLint oldStencilZPass = GL_KEEP; glGetIntegerv(GL_STENCIL_FUNC, &oldStencilFunc); glGetIntegerv(GL_STENCIL_REF, &oldStencilRef); glGetIntegerv(GL_STENCIL_VALUE_MASK, &oldStencilValueMask); glGetIntegerv(GL_STENCIL_WRITEMASK, &oldStencilWriteMask); glGetIntegerv(GL_STENCIL_FAIL, &oldStencilFail); glGetIntegerv(GL_STENCIL_PASS_DEPTH_FAIL, &oldStencilZFail); glGetIntegerv(GL_STENCIL_PASS_DEPTH_PASS, &oldStencilZPass); GLboolean colorMask[4] = { GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE }; glGetBooleanv(GL_COLOR_WRITEMASK, colorMask); GLint viewport[4] = { 0, 0, 1, 1 }; glGetIntegerv(GL_VIEWPORT, viewport);

    glEnable(GL_DEPTH_TEST); glDepthFunc(GL_LEQUAL); glDepthMask(GL_FALSE);
    glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glDisable(GL_CULL_FACE);

    if (useVBO && g_splatVBO.initialized && g_splatShader != 0) {
        if (g_splatVBO.ebo == 0) { LogRenderer("ERROR: EBO is 0, cannot draw!"); }
        else {
            GLint eboSizeCheck = 0; glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo); glGetBufferParameteriv(GL_ELEMENT_ARRAY_BUFFER, GL_BUFFER_SIZE, &eboSizeCheck); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);
            if (eboSizeCheck == 0 && g_splatSortIndices.size() > 0) { LogRenderer("WARN: EBO size is 0 but we have sort indices! Skipping draw."); }
            else if (eboSizeCheck > 0) {
                DrawSplatBuffer(g_splatVBO, mvpMatrix, 0.0f, 0.0f, (float)viewport[2], (float)viewport[3]);
                if (!g_highlightMaskVBO.indices.empty()) {
                    int activeHighlightMode = HIGHLIGHT_NONE;
                    for (const SplatObject& object : g_splatObjects) {
                        if (object.visible && object.highlight_mode != HIGHLIGHT_NONE) {
                            activeHighlightMode = object.highlight_mode;
                            break;
                        }
                    }

                    if (activeHighlightMode != HIGHLIGHT_NONE) {
                        if (EnsureOutlineTextures(viewport[2], viewport[3]) && EnsureOutlineQuad()) {
                            glBindFramebuffer(GL_FRAMEBUFFER, g_outlineMaskFBO);
                            glViewport(0, 0, viewport[2], viewport[3]);
                            glClearColor(0.0f, 0.0f, 0.0f, 0.0f);
                            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
                            glDisable(GL_DEPTH_TEST);
                            glDepthMask(GL_FALSE);
                            glDisable(GL_BLEND);
                            DrawSplatBuffer(
                                g_highlightMaskVBO,
                                mvpMatrix,
                                HighlightMaskAlphaCutoffForMode(activeHighlightMode),
                                0.0f,
                                (float)viewport[2],
                                (float)viewport[3],
                                true);

                            glBindFramebuffer(GL_FRAMEBUFFER, 0);
                            glViewport(viewport[0], viewport[1], viewport[2], viewport[3]);
                            glDisable(GL_DEPTH_TEST);
                            glEnable(GL_BLEND);
                            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
                            glUseProgram(g_outlineCompositeShader);
                            glActiveTexture(GL_TEXTURE0);
                            glBindTexture(GL_TEXTURE_2D, g_outlineMaskColorTex);
                            glUniform1i(glGetUniformLocation(g_outlineCompositeShader, "uMaskTex"), 0);
                            glUniform2f(glGetUniformLocation(g_outlineCompositeShader, "uTexelSize"), 1.0f / (float)viewport[2], 1.0f / (float)viewport[3]);
                            glUniform1f(glGetUniformLocation(g_outlineCompositeShader, "uThicknessPx"), HighlightOutlineThicknessPxForMode(activeHighlightMode));
                            glUniform1f(glGetUniformLocation(g_outlineCompositeShader, "uMergePx"), HighlightOutlineMergePxForMode(activeHighlightMode));
                            glUniform1f(glGetUniformLocation(g_outlineCompositeShader, "uGapPx"), HighlightOutlineGapPxForMode(activeHighlightMode));
                            glUniform1f(glGetUniformLocation(g_outlineCompositeShader, "uInnerThreshold"), 0.45f);
                            glUniform1f(glGetUniformLocation(g_outlineCompositeShader, "uOuterThreshold"), 0.12f);
                            float outline_r = 0.0f;
                            float outline_g = 0.0f;
                            float outline_b = 0.0f;
                            float outline_a = 1.0f;
                            HighlightColorForMode(activeHighlightMode, &outline_r, &outline_g, &outline_b, &outline_a);
                            glUniform4f(glGetUniformLocation(g_outlineCompositeShader, "uOutlineColor"), outline_r, outline_g, outline_b, 1.0f);
                            glBindVertexArray(g_outlineQuadVAO);
                            glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
                            glBindVertexArray(0);
                            glActiveTexture(GL_TEXTURE0);
                            glBindTexture(GL_TEXTURE_2D, 0);
                        }
                        else if (stencilBits > 0) {
                            glClearStencil(0);
                            glClear(GL_STENCIL_BUFFER_BIT);
                            glEnable(GL_STENCIL_TEST);
                            glStencilMask(0xFF);
                            glStencilFunc(GL_ALWAYS, 1, 0xFF);
                            glStencilOp(GL_KEEP, GL_KEEP, GL_REPLACE);
                            glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE);
                            glEnable(GL_DEPTH_TEST);
                            glDepthFunc(GL_LEQUAL);
                            glDepthMask(GL_FALSE);
                            glDisable(GL_BLEND);
                            DrawSplatBuffer(
                                g_highlightMaskVBO,
                                mvpMatrix,
                                HighlightMaskAlphaCutoffForMode(activeHighlightMode),
                                0.0f,
                                (float)viewport[2],
                                (float)viewport[3],
                                true);

                            glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
                            glStencilMask(0x00);
                            glStencilFunc(GL_NOTEQUAL, 1, 0xFF);
                            glStencilOp(GL_KEEP, GL_KEEP, GL_KEEP);
                            glDisable(GL_DEPTH_TEST);
                            glEnable(GL_BLEND);
                            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
                            DrawSplatBuffer(
                                g_highlightMaskVBO,
                                mvpMatrix,
                                0.5f,
                                HighlightOutlineExpandPxForMode(activeHighlightMode),
                                (float)viewport[2],
                                (float)viewport[3],
                                true);
                            glDisable(GL_STENCIL_TEST);
                        } else {
                            glDisable(GL_DEPTH_TEST);
                            glEnable(GL_BLEND);
                            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
                            DrawSplatBuffer(
                                g_highlightMaskVBO,
                                mvpMatrix,
                                0.5f,
                                HighlightOutlineExpandPxForMode(activeHighlightMode),
                                (float)viewport[2],
                                (float)viewport[3],
                                true);
                        }
                    }
                }
            }
        }
    }
    else if (!useVBO && hasView && hasProj) {
        LogRenderer("DEBUG: Using Immediate Mode fallback for %zu splats.", g_splatSortIndices.size());
        glUseProgram(0);
        float projectionMatrixGL[16]; float viewMatrixGL[16];
        TransposeMat4(projectionMatrix, projectionMatrixGL);
        TransposeMat4(viewMatrix, viewMatrixGL);
        glEnable(GL_TEXTURE_2D); glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, g_gaussTexture);
        glMatrixMode(GL_PROJECTION); glLoadMatrixf(projectionMatrixGL);
        glMatrixMode(GL_MODELVIEW); glLoadMatrixf(viewMatrixGL);
        RenderHighlightedSplatsIM(clipBoxState);
        for (GLuint splatIndex : g_splatSortIndices) { if (splatIndex < g_splats.size()) RenderSingleSplatIM(g_splats[splatIndex], viewMatrix, projectionMatrix); }
        glBindTexture(GL_TEXTURE_2D, 0);
        if (!texEn) glDisable(GL_TEXTURE_2D);
    }
    else {
        if (!useVBO) LogRenderer("DEBUG: Skipping render (VBO disabled or IM requirements not met [view:%d, proj:%d]).", hasView, hasProj);
        else if (!g_splatVBO.initialized) LogRenderer("DEBUG: Skipping render (VBO not initialized).");
        else if (g_splatShader == 0) LogRenderer("DEBUG: Skipping render (Shader not loaded).");
        else LogRenderer("DEBUG: Skipping render (Unknown reason).");
    }

    glUseProgram(oldProg); if (blendEn) glBlendFuncSeparate(oldBlendSrcRGB, oldBlendDstRGB, oldBlendSrcAlpha, oldBlendDstAlpha); else glDisable(GL_BLEND); if (depthEn) glDepthFunc(oldDepthFunc); else glDisable(GL_DEPTH_TEST); glDepthMask(depthMask); if (cullEn) { glEnable(GL_CULL_FACE); glCullFace(oldCullMode); }
    else glDisable(GL_CULL_FACE); glActiveTexture(oldActiveTex); glBindTexture(GL_TEXTURE_2D, oldTexBind); if (!texEn && oldTexBind == 0) glDisable(GL_TEXTURE_2D); else if (texEn) glEnable(GL_TEXTURE_2D);
    if (stencilEn) glEnable(GL_STENCIL_TEST); else glDisable(GL_STENCIL_TEST); glStencilFunc(oldStencilFunc, oldStencilRef, oldStencilValueMask); glStencilMask(oldStencilWriteMask); glStencilOp(oldStencilFail, oldStencilZFail, oldStencilZPass); glColorMask(colorMask[0], colorMask[1], colorMask[2], colorMask[3]);

    GLenum renderErr = glGetError(); if (renderErr != GL_NO_ERROR) LogRenderer("GL Error after render: 0x%x", renderErr);
}


void ConvertColor(float dc0, float dc1, float dc2, float& r, float& g, float& b) { auto sig = [](float x) {return 1.f / (1.f + exp(-x));}; r = sig(dc0); g = sig(dc1); b = sig(dc2); }
void ConvertScale(float s0, float s1, float& sx, float& sy) { float rx = exp(s0); float ry = exp(s1); float sf = 45.f; sx = rx * sf; sy = ry * sf; }
static int NormalizeImportUpAxis(int up_axis_mode) {
    switch (up_axis_mode) {
    case IMPORT_ORIENTATION_SWAP_A:
    case IMPORT_ORIENTATION_SWAP_B:
    case IMPORT_ORIENTATION_FLIP_Z:
    case IMPORT_ORIENTATION_RAW:
        return up_axis_mode;
    default:
        return IMPORT_ORIENTATION_LEGACY;
    }
}

static void MapImportPosition(const float* source_xyz, int up_axis_mode, float scale_distance, float* out_xyz) {
    switch (NormalizeImportUpAxis(up_axis_mode)) {
    case IMPORT_ORIENTATION_SWAP_A:
        out_xyz[0] = source_xyz[0] * scale_distance;
        out_xyz[1] = -source_xyz[2] * scale_distance;
        out_xyz[2] = source_xyz[1] * scale_distance;
        break;
    case IMPORT_ORIENTATION_SWAP_B:
        out_xyz[0] = source_xyz[0] * scale_distance;
        out_xyz[1] = source_xyz[2] * scale_distance;
        out_xyz[2] = -source_xyz[1] * scale_distance;
        break;
    case IMPORT_ORIENTATION_FLIP_Z:
        out_xyz[0] = source_xyz[0] * scale_distance;
        out_xyz[1] = source_xyz[1] * scale_distance;
        out_xyz[2] = -source_xyz[2] * scale_distance;
        break;
    case IMPORT_ORIENTATION_RAW:
        out_xyz[0] = source_xyz[0] * scale_distance;
        out_xyz[1] = source_xyz[1] * scale_distance;
        out_xyz[2] = source_xyz[2] * scale_distance;
        break;
    default:
        out_xyz[0] = source_xyz[0] * scale_distance;
        out_xyz[1] = -source_xyz[1] * scale_distance;
        out_xyz[2] = source_xyz[2] * scale_distance;
        break;
    }
}

static void MapImportVector(const float* source_xyz, int up_axis_mode, float* out_xyz) {
    switch (NormalizeImportUpAxis(up_axis_mode)) {
    case IMPORT_ORIENTATION_SWAP_A:
        out_xyz[0] = source_xyz[0];
        out_xyz[1] = -source_xyz[2];
        out_xyz[2] = source_xyz[1];
        break;
    case IMPORT_ORIENTATION_SWAP_B:
        out_xyz[0] = source_xyz[0];
        out_xyz[1] = source_xyz[2];
        out_xyz[2] = -source_xyz[1];
        break;
    case IMPORT_ORIENTATION_FLIP_Z:
        out_xyz[0] = source_xyz[0];
        out_xyz[1] = source_xyz[1];
        out_xyz[2] = -source_xyz[2];
        break;
    case IMPORT_ORIENTATION_RAW:
        out_xyz[0] = source_xyz[0];
        out_xyz[1] = source_xyz[1];
        out_xyz[2] = source_xyz[2];
        break;
    default:
        out_xyz[0] = source_xyz[0];
        out_xyz[1] = -source_xyz[1];
        out_xyz[2] = source_xyz[2];
        break;
    }
}

static void BuildMappedBasisVectors(const PLYGaussianPoint& point, float scale_x, float scale_y, int up_axis_mode, float* out_basis_x, float* out_basis_y) {
    GaussSplat source_splat = {};
    source_splat.scale[0] = scale_x;
    source_splat.scale[1] = scale_y;
    source_splat.rotation[0] = point.rotation[0];
    source_splat.rotation[1] = point.rotation[1];
    source_splat.rotation[2] = point.rotation[2];
    source_splat.rotation[3] = point.rotation[3];

    float source_basis_x[3] = {};
    float source_basis_y[3] = {};
    ComputeBasisVectors(source_splat, source_basis_x, source_basis_y);
    MapImportVector(source_basis_x, up_axis_mode, out_basis_x);
    MapImportVector(source_basis_y, up_axis_mode, out_basis_y);
}

static int BuildSplatObjectFromPLYData(const char* object_id, PLYGaussianPoint* points, int count, double* out_center_xyz, double* out_half_extents_xyz, int up_axis_mode) {
    if (!object_id || !points || count <= 0) {
        return 0;
    }

    SplatObject* existing = FindSplatObject(object_id);
    if (!existing) {
        SplatObject object;
        object.id = object_id;
        g_splatObjects.push_back(object);
        existing = &g_splatObjects.back();
    }

    SplatObject& object = *existing;
    object.local_splats.clear();
    object.local_splats.reserve(count);
    object.visible = true;
    object.highlight_mode = HIGHLIGHT_NONE;
    object.axes_xyz[0] = 1.0; object.axes_xyz[1] = 0.0; object.axes_xyz[2] = 0.0;
    object.axes_xyz[3] = 0.0; object.axes_xyz[4] = 1.0; object.axes_xyz[5] = 0.0;
    object.axes_xyz[6] = 0.0; object.axes_xyz[7] = 0.0; object.axes_xyz[8] = 1.0;

    float scaleDistance = 20.0f;
    float opacityMultiplier = 3.0f;
    bool haveBounds = false;
    double minX = 0.0, minY = 0.0, minZ = 0.0, maxX = 0.0, maxY = 0.0, maxZ = 0.0;

    for (int i = 0; i < count; ++i) {
        float mapped_position[3] = {};
        MapImportPosition(points[i].position, up_axis_mode, scaleDistance, mapped_position);
        const float x = mapped_position[0];
        const float y = mapped_position[1];
        const float z = mapped_position[2];

        if (!haveBounds) {
            minX = maxX = x;
            minY = maxY = y;
            minZ = maxZ = z;
            haveBounds = true;
        } else {
            minX = std::min(minX, static_cast<double>(x));
            minY = std::min(minY, static_cast<double>(y));
            minZ = std::min(minZ, static_cast<double>(z));
            maxX = std::max(maxX, static_cast<double>(x));
            maxY = std::max(maxY, static_cast<double>(y));
            maxZ = std::max(maxZ, static_cast<double>(z));
        }

        float r, g, b;
        ConvertColor(points[i].color[0], points[i].color[1], points[i].color[2], r, g, b);
        float scaleX, scaleY;
        ConvertScale(points[i].scale[0], points[i].scale[1], scaleX, scaleY);
        if (scaleX <= 0 || scaleY <= 0 || !std::isfinite(scaleX) || !std::isfinite(scaleY)) {
            continue;
        }

        GaussSplat splat = {};
        splat.position[0] = x;
        splat.position[1] = y;
        splat.position[2] = z;
        splat.color[0] = r;
        splat.color[1] = g;
        splat.color[2] = b;
        splat.color[3] = std::max(0.01f, std::min((1.0f / (1.0f + exp(-points[i].opacity))) * opacityMultiplier, 1.0f));
        splat.scale[0] = 1.0f;
        splat.scale[1] = 1.0f;
        splat.rotation[0] = 1.0f;
        splat.rotation[1] = 0.0f;
        splat.rotation[2] = 0.0f;
        splat.rotation[3] = 0.0f;
        splat.use_custom_basis = 1;
        BuildMappedBasisVectors(points[i], scaleX, scaleY, up_axis_mode, splat.basis_x, splat.basis_y);
        object.local_splats.push_back(splat);
    }

    if (!haveBounds || object.local_splats.empty()) {
        return 0;
    }

    object.center_xyz[0] = (minX + maxX) * 0.5;
    object.center_xyz[1] = (minY + maxY) * 0.5;
    object.center_xyz[2] = (minZ + maxZ) * 0.5;
    object.base_half_extents_xyz[0] = std::max((maxX - minX) * 0.5, 0.001);
    object.base_half_extents_xyz[1] = std::max((maxY - minY) * 0.5, 0.001);
    object.base_half_extents_xyz[2] = std::max((maxZ - minZ) * 0.5, 0.001);
    object.half_extents_xyz[0] = object.base_half_extents_xyz[0];
    object.half_extents_xyz[1] = object.base_half_extents_xyz[1];
    object.half_extents_xyz[2] = object.base_half_extents_xyz[2];

    for (GaussSplat& splat : object.local_splats) {
        splat.position[0] -= static_cast<float>(object.center_xyz[0]);
        splat.position[1] -= static_cast<float>(object.center_xyz[1]);
        splat.position[2] -= static_cast<float>(object.center_xyz[2]);
    }

    if (out_center_xyz) {
        out_center_xyz[0] = object.center_xyz[0];
        out_center_xyz[1] = object.center_xyz[1];
        out_center_xyz[2] = object.center_xyz[2];
    }
    if (out_half_extents_xyz) {
        out_half_extents_xyz[0] = object.half_extents_xyz[0];
        out_half_extents_xyz[1] = object.half_extents_xyz[1];
        out_half_extents_xyz[2] = object.half_extents_xyz[2];
    }

    RefreshWorldSplatsFromObjects();
    return 1;
}

extern "C" EXPORT void LoadSplatsFromPLYWithUpAxis(const char* filename, int up_axis_mode) { LogRenderer("Loading PLY: %s", filename); HMODULE plyDLL = LoadLibraryA("PlyImporter.dll"); if (!plyDLL) { LogRenderer("ERR Load PlyImporter %d", GetLastError()); return; } typedef int(*LPD)(const char*, PLYGaussianPoint**); typedef void(*FPD)(PLYGaussianPoint*); LPD load = (LPD)GetProcAddress(plyDLL, "LoadPLYData"); FPD free = (FPD)GetProcAddress(plyDLL, "FreePLYData"); if (!load || !free) { LogRenderer("ERR Find funcs PlyImporter"); FreeLibrary(plyDLL); return; } PLYGaussianPoint* pts = nullptr; int cnt = load(filename, &pts); if (cnt <= 0 || !pts) { LogRenderer("ERR: PLY load failed (count=%d).", cnt); } else { LogRenderer("Loaded %d pts.", cnt); ResetSplatObjects(); BuildSplatObjectFromPLYData("__legacy__", pts, cnt, nullptr, nullptr, up_axis_mode); } if (pts) free(pts); if (plyDLL) FreeLibrary(plyDLL); LogRenderer("PLY load finished."); }
extern "C" EXPORT void LoadSplatsFromPLY(const char* filename) { LoadSplatsFromPLYWithUpAxis(filename, IMPORT_ORIENTATION_SWAP_B); }
extern "C" EXPORT void AddSplatsFromPLYData(PLYGaussianPoint* points, int count) {
    LogRenderer("Adding %d splats from PLY data...", count);
    ResetSplatObjects();
    BuildSplatObjectFromPLYData("__legacy__", points, count, nullptr, nullptr, IMPORT_ORIENTATION_SWAP_B);
}

extern "C" EXPORT int LoadSplatObjectFromPLYWithUpAxis(const char* object_id, const char* filename, double* out_center_xyz, double* out_half_extents_xyz, int up_axis_mode) {
    LogRenderer("Loading PLY object '%s': %s", object_id ? object_id : "(null)", filename ? filename : "(null)");
    if (!object_id || !filename) {
        return 0;
    }

    HMODULE plyDLL = LoadLibraryA("PlyImporter.dll");
    if (!plyDLL) {
        LogRenderer("ERR Load PlyImporter %d", GetLastError());
        return 0;
    }

    typedef int(*LPD)(const char*, PLYGaussianPoint**);
    typedef void(*FPD)(PLYGaussianPoint*);
    LPD load = (LPD)GetProcAddress(plyDLL, "LoadPLYData");
    FPD free = (FPD)GetProcAddress(plyDLL, "FreePLYData");
    if (!load || !free) {
        FreeLibrary(plyDLL);
        return 0;
    }

    PLYGaussianPoint* pts = nullptr;
    int cnt = load(filename, &pts);
    int result = 0;
    if (cnt > 0 && pts) {
        result = BuildSplatObjectFromPLYData(object_id, pts, cnt, out_center_xyz, out_half_extents_xyz, up_axis_mode);
    }

    if (pts) {
        free(pts);
    }
    FreeLibrary(plyDLL);
    return result;
}

extern "C" EXPORT int LoadSplatObjectFromPLY(const char* object_id, const char* filename, double* out_center_xyz, double* out_half_extents_xyz) {
    return LoadSplatObjectFromPLYWithUpAxis(object_id, filename, out_center_xyz, out_half_extents_xyz, IMPORT_ORIENTATION_SWAP_B);
}

extern "C" EXPORT int SetSplatObjectTransform(const char* object_id, const double* center_xyz, const double* half_extents_xyz, const double* axes_xyz, int visible) {
    SplatObject* object = FindSplatObject(object_id);
    if (!object) {
        return 0;
    }

    if (center_xyz) {
        memcpy(object->center_xyz, center_xyz, sizeof(object->center_xyz));
    }
    if (half_extents_xyz) {
        memcpy(object->half_extents_xyz, half_extents_xyz, sizeof(object->half_extents_xyz));
    }
    if (axes_xyz) {
        memcpy(object->axes_xyz, axes_xyz, sizeof(object->axes_xyz));
    }
    object->visible = visible != 0;
    RefreshWorldSplatsFromObjects();
    return 1;
}

extern "C" EXPORT int SetSplatObjectHighlight(const char* object_id, int highlight_mode) {
    SplatObject* object = FindSplatObject(object_id);
    if (!object) {
        return 0;
    }

    object->highlight_mode =
        (highlight_mode == HIGHLIGHT_SELECTED) ? HIGHLIGHT_SELECTED :
        (highlight_mode == HIGHLIGHT_HOVER ? HIGHLIGHT_HOVER : HIGHLIGHT_NONE);
    g_outlineVBO.needsUpdate = true;
    return 1;
}

extern "C" EXPORT int RemoveSplatObject(const char* object_id) {
    if (!object_id) {
        return 0;
    }

    const auto newEnd = std::remove_if(
        g_splatObjects.begin(),
        g_splatObjects.end(),
        [object_id](const SplatObject& object) { return object.id == object_id; });
    if (newEnd == g_splatObjects.end()) {
        return 0;
    }

    g_splatObjects.erase(newEnd, g_splatObjects.end());
    RefreshWorldSplatsFromObjects();
    return 1;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved) {
    switch (ul_reason_for_call) {
    case DLL_PROCESS_ATTACH: LogRenderer("DLL_PROCESS_ATTACH"); LoadHookFunctions(); break;
    case DLL_PROCESS_DETACH: LogRenderer("DLL_PROCESS_DETACH"); break;
    case DLL_THREAD_ATTACH: break;
    case DLL_THREAD_DETACH: break;
    }
    return TRUE;
}

