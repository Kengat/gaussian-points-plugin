#ifndef POINTCLOUDRENDERER_H
#define POINTCLOUDRENDERER_H

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

// Матрицы из хука
extern __declspec(dllimport) float g_currentModelview[16];
extern __declspec(dllimport) float g_currentProjection[16];

#ifdef __cplusplus
extern "C" {
#endif

	// Функция для установки данных облака точек
	EXPORT void SetPointCloud(const double* points_in, int count);

	// Функция для рендеринга облака точек
	EXPORT void renderPointCloud();

#ifdef __cplusplus
}
#endif

#endif // POINTCLOUDRENDERER_H