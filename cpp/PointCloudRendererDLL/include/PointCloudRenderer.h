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
EXPORT int SetPointCloudObjectData(const char* object_id, const double* points_in, int count);
EXPORT int LoadPointCloudObjectFromGasp(const char* object_id, const char* filename, double* out_center_xyz, double* out_half_extents_xyz);
EXPORT int SetPointCloudObjectTransform(const char* object_id, const double* center_xyz, const double* half_extents_xyz, const double* axes_xyz, int visible);
EXPORT int SetPointCloudObjectHighlight(const char* object_id, int highlight_mode);
EXPORT int RemovePointCloudObject(const char* object_id);
EXPORT void ClearPointCloudObjects();
EXPORT void renderPointCloud();

#ifdef __cplusplus
}
#endif

#endif // POINTCLOUDRENDERER_H
