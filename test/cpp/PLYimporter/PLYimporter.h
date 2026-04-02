#ifndef PLYIMPORTER_H
#define PLYIMPORTER_H

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

// Структура для хранения данных гауссовой кляксы
struct PLYGaussianPoint {
    float position[3];    // x, y, z
    float normal[3];      // nx, ny, nz
    float color[3];       // f_dc_0, f_dc_1, f_dc_2
    float scale[3];       // scale_0, scale_1, scale_2
    float rotation[4];    // rot_0, rot_1, rot_2, rot_3 (кватернион)
    float opacity;        // opacity
    float f_rest[45];     // f_rest_0, f_rest_1, ... f_rest_44
};

#ifdef __cplusplus
extern "C" {
#endif

    // Анализ PLY файла с выводом в консоль
    EXPORT bool LoadPLYFile(const char* filename);

    // Загрузка данных из PLY файла в массив точек
    EXPORT int LoadPLYData(const char* filename, PLYGaussianPoint** points);

    // Освобождение памяти, выделенной под массив точек
    EXPORT void FreePLYData(PLYGaussianPoint* points);

#ifdef __cplusplus
}
#endif

#endif // PLYIMPORTER_H