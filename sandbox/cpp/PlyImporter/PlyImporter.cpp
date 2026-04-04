#define _CRT_SECURE_NO_WARNINGS
#include "PlyImporter.h"
#include <windows.h>
#include <fstream>
#include <string>
#include <vector>
#include <map>
#include <cstring>

// Функция логирования
static void LogMessage(const char* format, ...) {
    char buffer[1024];
    va_list args;
    va_start(args, format);
    vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    OutputDebugStringA(buffer);
}

// Структура для хранения заголовка PLY
struct PLYHeader {
    bool isBinary;
    bool isLittleEndian;
    int vertexCount;
    std::vector<std::string> propertyNames;
    std::map<std::string, size_t> propertyOffsets;
};

static bool TryOpenWithCodePage(std::ifstream& file, const char* filename, UINT codePage, const char* label) {
    int wideLength = MultiByteToWideChar(codePage, 0, filename, -1, nullptr, 0);
    if (wideLength <= 0) {
        return false;
    }

    std::vector<wchar_t> wideBuffer(static_cast<size_t>(wideLength));
    if (MultiByteToWideChar(codePage, 0, filename, -1, wideBuffer.data(), wideLength) <= 0) {
        return false;
    }

    file.open(static_cast<const wchar_t*>(wideBuffer.data()), std::ios::binary);
    if (file.is_open()) {
        LogMessage("[PlyImporter] Opened file using %s path decoding\n", label);
        return true;
    }

    return false;
}

static std::ifstream OpenPLYStream(const char* filename) {
    std::ifstream file;
    if (!filename || !filename[0]) {
        return file;
    }

    file.open(filename, std::ios::binary);
    if (file.is_open()) {
        LogMessage("[PlyImporter] Opened file using native narrow path\n");
        return file;
    }

    file.clear();
    if (TryOpenWithCodePage(file, filename, CP_UTF8, "UTF-8")) {
        return file;
    }

    file.clear();
    TryOpenWithCodePage(file, filename, CP_ACP, "ACP");
    return file;
}

// Функция для чтения заголовка PLY
bool ReadPLYHeader(std::ifstream& file, PLYHeader& header) {
    std::string line;

    // Проверяем первую строку - должна быть "ply"
    std::getline(file, line);
    if (line != "ply") {
        LogMessage("[PlyImporter] Not a valid PLY file, missing 'ply' header\n");
        return false;
    }

    // Читаем формат
    std::getline(file, line);
    if (line.find("format binary_little_endian") != std::string::npos) {
        header.isBinary = true;
        header.isLittleEndian = true;
        LogMessage("[PlyImporter] Format: binary little endian\n");
    }
    else if (line.find("format binary_big_endian") != std::string::npos) {
        header.isBinary = true;
        header.isLittleEndian = false;
        LogMessage("[PlyImporter] Format: binary big endian\n");
    }
    else if (line.find("format ascii") != std::string::npos) {
        header.isBinary = false;
        LogMessage("[PlyImporter] Format: ASCII\n");
    }
    else {
        LogMessage("[PlyImporter] Unknown PLY format: %s\n", line.c_str());
        return false;
    }

    header.vertexCount = 0;

    // Читаем остальной заголовок
    while (std::getline(file, line)) {
        if (line.find("element vertex") != std::string::npos) {
            // Получаем количество вершин
            sscanf(line.c_str(), "element vertex %d", &header.vertexCount);
            LogMessage("[PlyImporter] Vertex count: %d\n", header.vertexCount);
        }
        else if (line.find("property float") != std::string::npos) {
            // Получаем имя свойства
            std::string propertyName = line.substr(line.rfind(' ') + 1);
            header.propertyNames.push_back(propertyName);
            header.propertyOffsets[propertyName] = header.propertyNames.size() - 1;
            LogMessage("[PlyImporter] Property: %s (offset: %zu)\n",
                propertyName.c_str(), header.propertyOffsets[propertyName]);
        }
        else if (line == "end_header") {
            // Конец заголовка
            LogMessage("[PlyImporter] End of header, found %zu properties\n", header.propertyNames.size());
            return true;
        }
    }

    LogMessage("[PlyImporter] Error: Incomplete PLY header\n");
    return false;
}

// Функция для чтения float из бинарного файла с учетом endianness
float ReadFloat(std::ifstream& file, bool isLittleEndian) {
    float value;
    file.read(reinterpret_cast<char*>(&value), sizeof(float));

    // Если наша система little endian, а файл big endian (или наоборот),
    // нужно преобразовать порядок байтов
    bool systemIsLittleEndian = true;  // Большинство современных систем little endian

    if (systemIsLittleEndian != isLittleEndian) {
        char* bytes = reinterpret_cast<char*>(&value);
        std::swap(bytes[0], bytes[3]);
        std::swap(bytes[1], bytes[2]);
    }

    return value;
}

// Функция для чтения данных вершины
std::vector<float> ReadVertex(std::ifstream& file, const PLYHeader& header) {
    std::vector<float> vertexData(header.propertyNames.size());

    for (size_t i = 0; i < header.propertyNames.size(); i++) {
        vertexData[i] = ReadFloat(file, header.isLittleEndian);
    }

    return vertexData;
}

// Преобразование данных вершины в точку с гауссовыми параметрами
PLYGaussianPoint ConvertToPLYGaussianPoint(const std::vector<float>& vertexData, const PLYHeader& header) {
    PLYGaussianPoint point = { 0 };
    int f_rest_count = 0;

    // Позиция
    if (header.propertyOffsets.count("x") > 0 &&
        header.propertyOffsets.count("y") > 0 &&
        header.propertyOffsets.count("z") > 0) {
        point.position[0] = vertexData[header.propertyOffsets.at("x")];
        point.position[1] = vertexData[header.propertyOffsets.at("y")];
        point.position[2] = vertexData[header.propertyOffsets.at("z")];
    }

    // Нормаль
    if (header.propertyOffsets.count("nx") > 0 &&
        header.propertyOffsets.count("ny") > 0 &&
        header.propertyOffsets.count("nz") > 0) {
        point.normal[0] = vertexData[header.propertyOffsets.at("nx")];
        point.normal[1] = vertexData[header.propertyOffsets.at("ny")];
        point.normal[2] = vertexData[header.propertyOffsets.at("nz")];
    }

    // Цвет
    if (header.propertyOffsets.count("f_dc_0") > 0 &&
        header.propertyOffsets.count("f_dc_1") > 0 &&
        header.propertyOffsets.count("f_dc_2") > 0) {
        point.color[0] = vertexData[header.propertyOffsets.at("f_dc_0")];
        point.color[1] = vertexData[header.propertyOffsets.at("f_dc_1")];
        point.color[2] = vertexData[header.propertyOffsets.at("f_dc_2")];
    }

    // Масштаб
    if (header.propertyOffsets.count("scale_0") > 0 &&
        header.propertyOffsets.count("scale_1") > 0 &&
        header.propertyOffsets.count("scale_2") > 0) {
        point.scale[0] = vertexData[header.propertyOffsets.at("scale_0")];
        point.scale[1] = vertexData[header.propertyOffsets.at("scale_1")];
        point.scale[2] = vertexData[header.propertyOffsets.at("scale_2")];
    }

    // Поворот
    if (header.propertyOffsets.count("rot_0") > 0 &&
        header.propertyOffsets.count("rot_1") > 0 &&
        header.propertyOffsets.count("rot_2") > 0 &&
        header.propertyOffsets.count("rot_3") > 0) {
        point.rotation[0] = vertexData[header.propertyOffsets.at("rot_0")];
        point.rotation[1] = vertexData[header.propertyOffsets.at("rot_1")];
        point.rotation[2] = vertexData[header.propertyOffsets.at("rot_2")];
        point.rotation[3] = vertexData[header.propertyOffsets.at("rot_3")];
    }

    // Прозрачность
    if (header.propertyOffsets.count("opacity") > 0) {
        point.opacity = vertexData[header.propertyOffsets.at("opacity")];
    }

    // f_rest параметры
    for (int i = 0; i < 45; i++) {
        std::string propName = "f_rest_" + std::to_string(i);
        if (header.propertyOffsets.count(propName) > 0) {
            point.f_rest[i] = vertexData[header.propertyOffsets.at(propName)];
            ++f_rest_count;
        }
    }

    if (f_rest_count >= 45) {
        point.sh_degree = 3;
    }
    else if (f_rest_count >= 24) {
        point.sh_degree = 2;
    }
    else if (f_rest_count >= 9) {
        point.sh_degree = 1;
    }
    else {
        point.sh_degree = 0;
    }

    return point;
}

extern "C" EXPORT bool LoadPLYFile(const char* filename) {
    LogMessage("[PlyImporter] Loading PLY file: %s\n", filename);

    std::ifstream file = OpenPLYStream(filename);
    if (!file.is_open()) {
        LogMessage("[PlyImporter] Error: Could not open file\n");
        return false;
    }

    PLYHeader header;
    if (!ReadPLYHeader(file, header)) {
        LogMessage("[PlyImporter] Error: Failed to parse PLY header\n");
        return false;
    }

    if (header.vertexCount == 0) {
        LogMessage("[PlyImporter] Error: No vertices in file\n");
        return false;
    }

    // Читаем первую вершину и выводим ее свойства
    std::vector<float> firstVertex = ReadVertex(file, header);

    if (firstVertex.size() != header.propertyNames.size()) {
        LogMessage("[PlyImporter] Error: Vertex data size mismatch\n");
        return false;
    }

    LogMessage("[PlyImporter] First vertex properties:\n");

    // Позиция (x, y, z)
    if (header.propertyOffsets.count("x") > 0 &&
        header.propertyOffsets.count("y") > 0 &&
        header.propertyOffsets.count("z") > 0) {

        float x = firstVertex[header.propertyOffsets["x"]];
        float y = firstVertex[header.propertyOffsets["y"]];
        float z = firstVertex[header.propertyOffsets["z"]];
        LogMessage("  Position: (%.6f, %.6f, %.6f)\n", x, y, z);
    }

    // Нормаль (nx, ny, nz)
    if (header.propertyOffsets.count("nx") > 0 &&
        header.propertyOffsets.count("ny") > 0 &&
        header.propertyOffsets.count("nz") > 0) {

        float nx = firstVertex[header.propertyOffsets["nx"]];
        float ny = firstVertex[header.propertyOffsets["ny"]];
        float nz = firstVertex[header.propertyOffsets["nz"]];
        LogMessage("  Normal: (%.6f, %.6f, %.6f)\n", nx, ny, nz);
    }

    // Цвет (f_dc_0, f_dc_1, f_dc_2)
    if (header.propertyOffsets.count("f_dc_0") > 0 &&
        header.propertyOffsets.count("f_dc_1") > 0 &&
        header.propertyOffsets.count("f_dc_2") > 0) {

        float r = firstVertex[header.propertyOffsets["f_dc_0"]];
        float g = firstVertex[header.propertyOffsets["f_dc_1"]];
        float b = firstVertex[header.propertyOffsets["f_dc_2"]];
        LogMessage("  Color (f_dc): (%.6f, %.6f, %.6f)\n", r, g, b);
    }

    // Масштаб (scale_0, scale_1, scale_2)
    if (header.propertyOffsets.count("scale_0") > 0 &&
        header.propertyOffsets.count("scale_1") > 0 &&
        header.propertyOffsets.count("scale_2") > 0) {

        float sx = firstVertex[header.propertyOffsets["scale_0"]];
        float sy = firstVertex[header.propertyOffsets["scale_1"]];
        float sz = firstVertex[header.propertyOffsets["scale_2"]];
        LogMessage("  Scale: (%.6f, %.6f, %.6f)\n", sx, sy, sz);
    }

    // Вращение (rot_0, rot_1, rot_2, rot_3)
    if (header.propertyOffsets.count("rot_0") > 0 &&
        header.propertyOffsets.count("rot_1") > 0 &&
        header.propertyOffsets.count("rot_2") > 0 &&
        header.propertyOffsets.count("rot_3") > 0) {

        float r0 = firstVertex[header.propertyOffsets["rot_0"]];
        float r1 = firstVertex[header.propertyOffsets["rot_1"]];
        float r2 = firstVertex[header.propertyOffsets["rot_2"]];
        float r3 = firstVertex[header.propertyOffsets["rot_3"]];
        LogMessage("  Rotation (quaternion): (%.6f, %.6f, %.6f, %.6f)\n", r0, r1, r2, r3);
    }

    // Прозрачность
    if (header.propertyOffsets.count("opacity") > 0) {
        float opacity = firstVertex[header.propertyOffsets["opacity"]];
        LogMessage("  Opacity: %.6f\n", opacity);
    }

    // Выводим первые несколько f_rest параметров
    for (int i = 0; i < 5; i++) {
        std::string propName = "f_rest_" + std::to_string(i);
        if (header.propertyOffsets.count(propName) > 0) {
            float value = firstVertex[header.propertyOffsets[propName]];
            LogMessage("  %s: %.6f\n", propName.c_str(), value);
        }
    }

    LogMessage("[PlyImporter] PLY file loaded and analyzed successfully\n");
    return true;
}

// Новая функция для загрузки всех данных
extern "C" EXPORT int LoadPLYData(const char* filename, PLYGaussianPoint** points) {
    LogMessage("[PlyImporter] Loading PLY data from: %s\n", filename);

    std::ifstream file = OpenPLYStream(filename);
    if (!file.is_open()) {
        LogMessage("[PlyImporter] Error: Could not open file\n");
        return 0;
    }

    PLYHeader header;
    if (!ReadPLYHeader(file, header)) {
        LogMessage("[PlyImporter] Error: Failed to parse PLY header\n");
        return 0;
    }

    // Выделяем память для массива точек
    *points = new PLYGaussianPoint[header.vertexCount];
    if (!(*points)) {
        LogMessage("[PlyImporter] Error: Memory allocation failed\n");
        return 0;
    }

    // Читаем все вершины
    for (int i = 0; i < header.vertexCount; i++) {
        std::vector<float> vertexData = ReadVertex(file, header);
        (*points)[i] = ConvertToPLYGaussianPoint(vertexData, header);
    }

    LogMessage("[PlyImporter] Successfully loaded %d points\n", header.vertexCount);
    return header.vertexCount;
}

// Освобождение памяти
extern "C" EXPORT void FreePLYData(PLYGaussianPoint* points) {
    if (points) {
        delete[] points;
        LogMessage("[PlyImporter] PLY data memory freed\n");
    }
}

extern "C" EXPORT int GetPLYGaussianPointSize() {
    return static_cast<int>(sizeof(PLYGaussianPoint));
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved) {
    switch (ul_reason_for_call) {
    case DLL_PROCESS_ATTACH:
        LogMessage("[PlyImporter] DLL_PROCESS_ATTACH\n");
        break;
    case DLL_PROCESS_DETACH:
        LogMessage("[PlyImporter] DLL_PROCESS_DETACH\n");
        break;
    }
    return TRUE;
}



