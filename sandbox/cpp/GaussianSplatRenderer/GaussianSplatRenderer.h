#ifndef GAUSSIAN_SPLAT_RENDERER_H
#define GAUSSIAN_SPLAT_RENDERER_H

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

// Структура для хранения данных гауссовой кляксы из PLY файла
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

    // Функция для установки данных облака точек
    EXPORT void SetPointCloud(const double* points_in, int count);

    // Функция для рендеринга облака точек
    EXPORT void renderPointCloud();

    // Функция для добавления новой кляксы
    EXPORT void AddSplat(float x, float y, float z,
        float r, float g, float b, float a,
        float scaleX, float scaleY, float rotation, bool rotateVertical);

    // Функция для очистки всех клякс
    EXPORT void ClearSplats();

    // Новые функции для работы с PLY
    EXPORT void LoadSplatsFromPLY(const char* filename);
    EXPORT void AddSplatsFromPLYData(PLYGaussianPoint* points, int count);

#ifdef __cplusplus
}
#endif

#endif // GAUSSIAN_SPLAT_RENDERER_H
