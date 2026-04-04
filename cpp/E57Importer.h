#ifndef E57IMPORTER_H
#define E57IMPORTER_H

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

#include <cstdint>

extern "C" {

    EXPORT int GetE57PointCount(const char* filename);
    EXPORT int importE57(const char* filename);
    EXPORT int StartImportE57Async(const char* filename);
    EXPORT void GetImportE57Status(
        int* state,
        int* total_points,
        int* processed_points,
        int* result_count);
    EXPORT int GetImportE57Error(char* buffer, int buffer_size);

    EXPORT bool getPointData(int index,
        double* x, double* y, double* z,
        uint8_t* r, uint8_t* g, uint8_t* b);
}

#endif
