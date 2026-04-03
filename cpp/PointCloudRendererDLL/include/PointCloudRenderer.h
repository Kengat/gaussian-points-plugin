#ifndef POINTCLOUDRENDERER_H
#define POINTCLOUDRENDERER_H

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif
EXPORT void SetPointCloud(const double* points_in, int count);
EXPORT void renderPointCloud();

#ifdef __cplusplus
}
#endif

#endif // POINTCLOUDRENDERER_H
