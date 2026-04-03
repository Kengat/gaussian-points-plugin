#include "GaussianSplatRenderer.h"
#include <windows.h>
#include <GL/glew.h>
#include <GL/gl.h>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <fstream>
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
static PFN_GET_MATRIX_BY_LOC GetMatrixByLocation = nullptr;
static PFN_GET_CAMERA_STATE GetCameraState = nullptr;

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
};
static std::vector<GaussSplat> g_splats;

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

static GLuint g_splatShader = 0;

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

static GLuint CreateSplatShader() {
    LogRenderer("Creating splat shader...");
    const char* vertexShaderSource = R"(#version 130
        attribute vec3 aPos; attribute vec2 aTexCoord; attribute vec4 aColor;
        uniform mat4 uMVP;
        varying vec2 vTexCoord; varying vec4 vColor;
        void main() { gl_Position = uMVP * vec4(aPos, 1.0); vTexCoord = aTexCoord; vColor = aColor; })";
    const char* fragmentShaderSource = R"(#version 130
        varying vec2 vTexCoord; varying vec4 vColor; uniform sampler2D uTexture;
        void main() { vec4 texColor = texture2D(uTexture, vTexCoord); gl_FragColor = vec4(vColor.rgb, texColor.a * vColor.a); })";

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

    glGenVertexArrays(1, &g_splatVBO.vao); glBindVertexArray(g_splatVBO.vao);
    glGenBuffers(1, &g_splatVBO.vbo); glBindBuffer(GL_ARRAY_BUFFER, g_splatVBO.vbo);
    glGenBuffers(1, &g_splatVBO.ebo); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, sizeof(SplatVBOData::VertexData), (void*)offsetof(SplatVBOData::VertexData, position)); glEnableVertexAttribArray(0);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, sizeof(SplatVBOData::VertexData), (void*)offsetof(SplatVBOData::VertexData, texCoord)); glEnableVertexAttribArray(1);
    glVertexAttribPointer(2, 4, GL_FLOAT, GL_FALSE, sizeof(SplatVBOData::VertexData), (void*)offsetof(SplatVBOData::VertexData, color)); glEnableVertexAttribArray(2);
    glBindBuffer(GL_ARRAY_BUFFER, 0); glBindVertexArray(0);

    if (g_splatShader == 0) { g_splatShader = CreateSplatShader(); }
    if (g_splatShader == 0) { LogRenderer("ERROR: Shader creation failed during VBO init. VBO unusable."); return; }

    g_splatVBO.initialized = true; g_splatVBO.needsUpdate = true;
    LogRenderer("Splat VBO initialized successfully (VAO:%u, VBO:%u, EBO:%u).", g_splatVBO.vao, g_splatVBO.vbo, g_splatVBO.ebo);
}

static void UpdateSplatVBOVertices() {
    if (!g_splatVBO.initialized) { LogRenderer("DEBUG: UpdateVBOVertices skip - not inited"); return; }
    if (!g_splatVBO.needsUpdate) { return; }
    if (g_splats.empty() && g_splatVBO.vertices.empty()) { g_splatVBO.needsUpdate = false; return; }

    LogRenderer("DEBUG: Entering UpdateSplatVBOVertices for %zu splats...", g_splats.size());

    if (g_splats.empty()) {
        if (g_splatVBO.vao != 0) {
            glBindVertexArray(g_splatVBO.vao);
            glBindBuffer(GL_ARRAY_BUFFER, g_splatVBO.vbo); glBufferData(GL_ARRAY_BUFFER, 0, nullptr, GL_STATIC_DRAW);
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo); glBufferData(GL_ELEMENT_ARRAY_BUFFER, 0, nullptr, GL_DYNAMIC_DRAW);
            glBindVertexArray(0);
            g_splatVBO.vertices.clear(); g_splatVBO.indices.clear(); g_splatSortIndices.clear();
            LogRenderer("DEBUG: Cleared VBO/EBO/Indices as splats are empty.");
        }
    }
    else {
        g_splatVBO.vertices.clear();
        g_splatVBO.vertices.reserve(g_splats.size() * 4);
        float quadSize = 1.0f;
        for (size_t i = 0; i < g_splats.size(); ++i) {
            const GaussSplat& splat = g_splats[i];
            float rotMatrix[16]; QuaternionToMatrix(splat.rotation[0], splat.rotation[1], splat.rotation[2], splat.rotation[3], rotMatrix);
            float localXVec[3] = { quadSize * splat.scale[0],0,0 }; float localYVec[3] = { 0,quadSize * splat.scale[1],0 };
            float rotatedXVec[3], rotatedYVec[3];
            rotatedXVec[0] = localXVec[0] * rotMatrix[0] + localYVec[0] * rotMatrix[4]; rotatedXVec[1] = localXVec[0] * rotMatrix[1] + localYVec[0] * rotMatrix[5]; rotatedXVec[2] = localXVec[0] * rotMatrix[2] + localYVec[0] * rotMatrix[6];
            rotatedYVec[0] = localXVec[1] * rotMatrix[0] + localYVec[1] * rotMatrix[4]; rotatedYVec[1] = localXVec[1] * rotMatrix[1] + localYVec[1] * rotMatrix[5]; rotatedYVec[2] = localXVec[1] * rotMatrix[2] + localYVec[1] * rotMatrix[6];
            SplatVBOData::VertexData v0, v1, v2, v3;
            v0.position[0] = splat.position[0] - rotatedXVec[0] - rotatedYVec[0]; v0.position[1] = splat.position[1] - rotatedXVec[1] - rotatedYVec[1]; v0.position[2] = splat.position[2] - rotatedXVec[2] - rotatedYVec[2]; v0.texCoord[0] = 0.0f; v0.texCoord[1] = 0.0f;
            v1.position[0] = splat.position[0] + rotatedXVec[0] - rotatedYVec[0]; v1.position[1] = splat.position[1] + rotatedXVec[1] - rotatedYVec[1]; v1.position[2] = splat.position[2] + rotatedXVec[2] - rotatedYVec[2]; v1.texCoord[0] = 1.0f; v1.texCoord[1] = 0.0f;
            v2.position[0] = splat.position[0] + rotatedXVec[0] + rotatedYVec[0]; v2.position[1] = splat.position[1] + rotatedXVec[1] + rotatedYVec[1]; v2.position[2] = splat.position[2] + rotatedXVec[2] + rotatedYVec[2]; v2.texCoord[0] = 1.0f; v2.texCoord[1] = 1.0f;
            v3.position[0] = splat.position[0] - rotatedXVec[0] + rotatedYVec[0]; v3.position[1] = splat.position[1] - rotatedXVec[1] + rotatedYVec[1]; v3.position[2] = splat.position[2] - rotatedXVec[2] + rotatedYVec[2]; v3.texCoord[0] = 0.0f; v3.texCoord[1] = 1.0f;
            v0.color[0] = v1.color[0] = v2.color[0] = v3.color[0] = splat.color[0]; v0.color[1] = v1.color[1] = v2.color[1] = v3.color[1] = splat.color[1]; v0.color[2] = v1.color[2] = v2.color[2] = v3.color[2] = splat.color[2]; v0.color[3] = v1.color[3] = v2.color[3] = v3.color[3] = splat.color[3];
            g_splatVBO.vertices.push_back(v0); g_splatVBO.vertices.push_back(v1); g_splatVBO.vertices.push_back(v2); g_splatVBO.vertices.push_back(v3);
        }
        glBindVertexArray(g_splatVBO.vao);
        glBindBuffer(GL_ARRAY_BUFFER, g_splatVBO.vbo);
        glBufferData(GL_ARRAY_BUFFER, g_splatVBO.vertices.size() * sizeof(SplatVBOData::VertexData), g_splatVBO.vertices.data(), GL_STATIC_DRAW);
        glBindVertexArray(0);

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

static void InitializeDefaultSplats() { LogRenderer("Default splat disabled."); g_splats.clear(); g_splatSortIndices.clear(); g_splatVBO.needsUpdate = true; }
static void EnsureTextureInitialized() { if (!g_textureInitialized) { g_gaussTexture = CreateGaussianTexture(64, 0.3f); g_textureInitialized = true; if (g_gaussTexture == 0) LogRenderer("ERR: Gauss texture failed."); } }
static void LoadHookFunctions() {
    if (GetMatrixByLocation && GetCameraState) return;
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
extern "C" EXPORT void AddSplat(float x, float y, float z, float r, float g, float b, float a, float scaleX, float scaleY, float rotation, bool rotateVertical) { GaussSplat s; s.position[0] = x;s.position[1] = y;s.position[2] = z; s.color[0] = r;s.color[1] = g;s.color[2] = b;s.color[3] = a; s.scale[0] = scaleX;s.scale[1] = scaleY; float an = rotation * M_PI / 180.f, ha = an * .5f, sn = sin(ha), cn = cos(ha); s.rotation[0] = cn; if (rotateVertical) { s.rotation[1] = sn;s.rotation[2] = 0;s.rotation[3] = 0; } else { s.rotation[1] = 0;s.rotation[2] = 0;s.rotation[3] = sn; } g_splats.push_back(s); g_splatVBO.needsUpdate = true; }
extern "C" EXPORT void AddSplatWithQuaternion(float x, float y, float z, float r, float g, float b, float a, float scaleX, float scaleY, float qw, float qx, float qy, float qz) { GaussSplat s; s.position[0] = x;s.position[1] = y;s.position[2] = z; s.color[0] = r;s.color[1] = g;s.color[2] = b;s.color[3] = std::max(0.01f, std::min(a, 1.f)); s.scale[0] = scaleX;s.scale[1] = scaleY; float n = sqrt(qw * qw + qx * qx + qy * qy + qz * qz); if (n > 1e-5f) { s.rotation[0] = qw / n;s.rotation[1] = qx / n;s.rotation[2] = qy / n;s.rotation[3] = qz / n; } else { s.rotation[0] = 1;s.rotation[1] = 0;s.rotation[2] = 0;s.rotation[3] = 0; } g_splats.push_back(s); g_splatVBO.needsUpdate = true; }
extern "C" EXPORT void ClearSplats() { LogRenderer("ClearSplats called."); g_splats.clear(); g_splatSortIndices.clear(); g_splatVBO.needsUpdate = true; }
extern "C" EXPORT void SetSplatSortingMode(SplatSortingMode mode) { if (mode >= 0 && mode <= 5) { g_sortingMode = mode; LogRenderer("Sort mode set %d.", mode); } else LogRenderer("Invalid sort mode %d.", mode); }

static void RenderSingleSplatIM(const GaussSplat& splat, const float* viewMatrix, const float* projectionMatrix) { glPushMatrix(); glTranslatef(splat.position[0], splat.position[1], splat.position[2]); float rm[16]; QuaternionToMatrix(splat.rotation[0], splat.rotation[1], splat.rotation[2], splat.rotation[3], rm); glMultMatrixf(rm); glScalef(splat.scale[0], splat.scale[1], 1.0f); glColor4f(splat.color[0], splat.color[1], splat.color[2], splat.color[3]); float qs = 1.0f; glBegin(GL_QUADS); glTexCoord2f(0, 0);glVertex3f(-qs, -qs, 0); glTexCoord2f(1, 0);glVertex3f(qs, -qs, 0); glTexCoord2f(1, 1);glVertex3f(qs, qs, 0); glTexCoord2f(0, 1);glVertex3f(-qs, qs, 0); glEnd(); glPopMatrix(); }
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

    bool sorting_done = false;
    if (hasCamera) {
        if (g_splatSortIndices.size() != g_splats.size()) {
            g_splatSortIndices.resize(g_splats.size()); std::iota(g_splatSortIndices.begin(), g_splatSortIndices.end(), 0);
            LogRenderer("DEBUG: Resized sort indices to %zu in sort block.", g_splatSortIndices.size());
            sorting_done = true;
        }

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

        if (posDistSq > 0.0001f || dirDiff > 0.000001f || g_framesSinceLastSort >= SORT_EVERY_N_FRAMES) {
            needSorting = true;

            memcpy(g_lastCamPos, camPos, 3 * sizeof(float));
            memcpy(g_lastViewDir, viewDir, 3 * sizeof(float));

            g_framesSinceLastSort = 0;
        }

        if (needSorting) {
            LogRenderer("DEBUG: Sorting %zu indices for view changes or frame limit...", g_splats.size());

            g_splatSortCache.resize(g_splats.size());

            // Cache the current camera-relative ordering so alpha blending stays stable.
            for (size_t i = 0; i < g_splats.size(); ++i) {
                const GaussSplat& splat = g_splats[i];
                SplatSortData& sortData = g_splatSortCache[i];

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
            }

            std::sort(g_splatSortCache.begin(), g_splatSortCache.end(),
                [](const SplatSortData& a, const SplatSortData& b) -> bool {
                    if (a.isBackfacing != b.isBackfacing) return a.isBackfacing;

                    if (a.isBackfacing) return a.distanceSquared > b.distanceSquared;

                    return a.sortKey > b.sortKey;
                });

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

    GLint oldProg = 0; glGetIntegerv(GL_CURRENT_PROGRAM, &oldProg); GLboolean blendEn = glIsEnabled(GL_BLEND); GLint oldBlendSrcRGB, oldBlendDstRGB, oldBlendSrcAlpha, oldBlendDstAlpha; glGetIntegerv(GL_BLEND_SRC_RGB, &oldBlendSrcRGB); glGetIntegerv(GL_BLEND_DST_RGB, &oldBlendDstRGB); glGetIntegerv(GL_BLEND_SRC_ALPHA, &oldBlendSrcAlpha); glGetIntegerv(GL_BLEND_DST_ALPHA, &oldBlendDstAlpha); GLboolean depthEn = glIsEnabled(GL_DEPTH_TEST); GLboolean depthMask; glGetBooleanv(GL_DEPTH_WRITEMASK, &depthMask); GLint oldDepthFunc; glGetIntegerv(GL_DEPTH_FUNC, &oldDepthFunc); GLboolean cullEn = glIsEnabled(GL_CULL_FACE); GLint oldCullMode; glGetIntegerv(GL_CULL_FACE_MODE, &oldCullMode); GLboolean texEn = glIsEnabled(GL_TEXTURE_2D); GLint oldActiveTex = 0; glGetIntegerv(GL_ACTIVE_TEXTURE, &oldActiveTex); GLint oldTexBind = 0; glGetIntegerv(GL_TEXTURE_BINDING_2D, &oldTexBind);

    glEnable(GL_DEPTH_TEST); glDepthFunc(GL_LEQUAL); glDepthMask(GL_FALSE);
    glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glDisable(GL_CULL_FACE);

    if (useVBO && g_splatVBO.initialized && g_splatShader != 0) {
        if (g_splatVBO.ebo == 0) { LogRenderer("ERROR: EBO is 0, cannot draw!"); }
        else {
            GLint eboSizeCheck = 0; glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo); glGetBufferParameteriv(GL_ELEMENT_ARRAY_BUFFER, GL_BUFFER_SIZE, &eboSizeCheck); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);
            if (eboSizeCheck == 0 && g_splatSortIndices.size() > 0) { LogRenderer("WARN: EBO size is 0 but we have sort indices! Skipping draw."); }
            else if (eboSizeCheck > 0) {
                glUseProgram(g_splatShader);
                GLint mvpLoc = glGetUniformLocation(g_splatShader, "uMVP"); if (mvpLoc != -1) glUniformMatrix4fv(mvpLoc, 1, GL_TRUE, mvpMatrix); else LogRenderer("ERR uMVP loc");
                GLint texLoc = glGetUniformLocation(g_splatShader, "uTexture"); if (texLoc != -1 && g_gaussTexture != 0) { glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, g_gaussTexture); glUniform1i(texLoc, 0); }
                else LogRenderer("WARN uTex loc/tex (ID:%u)", g_gaussTexture);
                glBindVertexArray(g_splatVBO.vao);
                glDrawElements(GL_TRIANGLES, (GLsizei)(eboSizeCheck / sizeof(GLuint)), GL_UNSIGNED_INT, 0);
                glBindVertexArray(0);
                if (texLoc != -1) { glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, 0); }
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

    GLenum renderErr = glGetError(); if (renderErr != GL_NO_ERROR) LogRenderer("GL Error after render: 0x%x", renderErr);
}


void ConvertColor(float dc0, float dc1, float dc2, float& r, float& g, float& b) { auto sig = [](float x) {return 1.f / (1.f + exp(-x));}; r = sig(dc0); g = sig(dc1); b = sig(dc2); }
void ConvertScale(float s0, float s1, float& sx, float& sy) { float rx = exp(s0); float ry = exp(s1); float sf = 45.f; sx = rx * sf; sy = ry * sf; }
extern "C" EXPORT void LoadSplatsFromPLY(const char* filename) { LogRenderer("Loading PLY: %s", filename); HMODULE plyDLL = LoadLibraryA("PlyImporter.dll"); if (!plyDLL) { LogRenderer("ERR Load PlyImporter %d", GetLastError()); return; } typedef int(*LPD)(const char*, PLYGaussianPoint**); typedef void(*FPD)(PLYGaussianPoint*); LPD load = (LPD)GetProcAddress(plyDLL, "LoadPLYData"); FPD free = (FPD)GetProcAddress(plyDLL, "FreePLYData"); if (!load || !free) { LogRenderer("ERR Find funcs PlyImporter"); FreeLibrary(plyDLL); return; } PLYGaussianPoint* pts = nullptr; int cnt = load(filename, &pts); if (cnt <= 0 || !pts) { LogRenderer("ERR: PLY load failed (count=%d).", cnt); } else { LogRenderer("Loaded %d pts.", cnt); AddSplatsFromPLYData(pts, cnt); } if (pts) free(pts); if (plyDLL) FreeLibrary(plyDLL); LogRenderer("PLY load finished."); }
extern "C" EXPORT void AddSplatsFromPLYData(PLYGaussianPoint* points, int count) {
    LogRenderer("Adding %d splats from PLY data...", count); ClearSplats(); g_splats.reserve(count);
    // Imported splats come in a tiny local space, so scale them up for SketchUp units.
    float scaleDistance = 20.0f; float opacityMultiplier = 3.0f; int addedCount = 0;
    for (int i = 0; i < count; ++i) {
        float x = points[i].position[0] * scaleDistance; float y = -points[i].position[1] * scaleDistance; float z = points[i].position[2] * scaleDistance;
        float r, g, b; ConvertColor(points[i].color[0], points[i].color[1], points[i].color[2], r, g, b);
        float scaleX, scaleY; ConvertScale(points[i].scale[0], points[i].scale[1], scaleX, scaleY);
        if (scaleX <= 0 || scaleY <= 0 || !std::isfinite(scaleX) || !std::isfinite(scaleY)) continue;
        float opacity = 1.0f / (1.0f + exp(-points[i].opacity)); opacity *= opacityMultiplier; opacity = std::max(0.01f, std::min(opacity, 1.0f));
        float qw = points[i].rotation[0]; float qx = points[i].rotation[1]; float qy = points[i].rotation[2]; float qz = points[i].rotation[3];
        AddSplatWithQuaternion(x, y, z, r, g, b, opacity, scaleX, scaleY, qw, qx, qy, qz); addedCount++;
    } g_splatVBO.needsUpdate = true; LogRenderer("Added %d splats. Total: %zu.", addedCount, g_splats.size());
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

