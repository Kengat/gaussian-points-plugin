#ifndef GAUSSIAN_SPLAT_RENDERER_H
#define GAUSSIAN_SPLAT_RENDERER_H

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

// Data layout for a single Gaussian splat loaded from a PLY file.
struct PLYGaussianPoint {
    float position[3];    // x, y, z
    float normal[3];      // nx, ny, nz
    float color[3];       // f_dc_0, f_dc_1, f_dc_2
    float scale[3];       // scale_0, scale_1, scale_2
    float rotation[4];    // rot_0, rot_1, rot_2, rot_3 (quaternion)
    float opacity;        // opacity
    float f_rest[45];     // f_rest_0, f_rest_1, ... f_rest_44
};

#ifdef __cplusplus
extern "C" {
#endif

    // Upload point cloud data stored as xyzrgb tuples.
    EXPORT void SetPointCloud(const double* points_in, int count);

    // Render the currently loaded point cloud and splats.
    EXPORT void renderPointCloud();

    // Add a single gaussian splat with Euler-style rotation input.
    EXPORT void AddSplat(float x, float y, float z,
        float r, float g, float b, float a,
        float scaleX, float scaleY, float rotation, bool rotateVertical);

    // Remove every currently loaded splat.
    EXPORT void ClearSplats();

    // Load splats from an on-disk PLY file or from already decoded data.
    EXPORT void LoadSplatsFromPLY(const char* filename);
    EXPORT void AddSplatsFromPLYData(PLYGaussianPoint* points, int count);
    EXPORT int LoadSplatObjectFromPLY(const char* object_id, const char* filename, double* out_center_xyz, double* out_half_extents_xyz);
    EXPORT int SetSplatObjectTransform(const char* object_id, const double* center_xyz, const double* half_extents_xyz, const double* axes_xyz, int visible);
    EXPORT int SetSplatObjectHighlight(const char* object_id, int highlight_mode);
    EXPORT int RemoveSplatObject(const char* object_id);
    EXPORT void ClearSplatObjects();

    // Report an axis-aligned bounds box for the loaded splats.
    EXPORT int GetSplatBounds(double* out_min_xyz, double* out_max_xyz);

#ifdef __cplusplus
}
#endif

#endif // GAUSSIAN_SPLAT_RENDERER_H
