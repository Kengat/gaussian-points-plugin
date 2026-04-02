#ifndef E57IMPORTER_H
#define E57IMPORTER_H

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

#include <cstdint> // for uint8_t

extern "C" {

    // importE57: читает .e57, возвращает кол-во точек или -1 при ошибке
    EXPORT int importE57(const char* filename);

    // getPointData: i, указатели на x,y,z,r,g,b. false если i вне диапазона
    EXPORT bool getPointData(int index,
        double* x, double* y, double* z,
        uint8_t* r, uint8_t* g, uint8_t* b);
}

#endif
