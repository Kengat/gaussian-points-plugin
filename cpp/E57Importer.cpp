// E57Importer.cpp

#include "E57Importer.h"
#include <E57SimpleReader.h>
#include <E57SimpleData.h>
#include <vector>
#include <string>
#include <iostream>
#include <stdexcept>
#include <algorithm>

struct E57Point {
    double x, y, z;
    uint8_t r, g, b;
};

static std::vector<E57Point> g_points;

extern "C" {

    int importE57(const char* filename)
    {
        g_points.clear();
        try {
            e57::ReaderOptions options;  // Можно задать дополнительные опции при необходимости
            e57::Reader reader(filename, options);
            if (!reader.IsOpen()) {
                std::cerr << "[importE57] Can't open: " << filename << "\n";
                return -1;
            }

            int64_t data3DCount = reader.GetData3DCount();
            if (data3DCount < 1) {
                reader.Close();
                return 0;
            }

            e57::Data3D d3d;
            if (!reader.ReadData3D(0, d3d)) {
                std::cerr << "[importE57] ReadData3D(0) failed\n";
                reader.Close();
                return -1;
            }

            // Размер "пачки" точек
            const size_t BATCH = 10000;

            // Выделяем буферы для координат и цветовых каналов
            std::vector<double>   xVals(BATCH), yVals(BATCH), zVals(BATCH);
            std::vector<uint16_t> rVals(BATCH), gVals(BATCH), bVals(BATCH);
            std::vector<int8_t>   colorInvalid(BATCH, 0);

            e57::Data3DPointsDouble buffers;
            buffers.cartesianX = xVals.data();
            buffers.cartesianY = yVals.data();
            buffers.cartesianZ = zVals.data();
            buffers.colorRed = rVals.data();
            buffers.colorGreen = gVals.data();
            buffers.colorBlue = bVals.data();
            buffers.isColorInvalid = colorInvalid.data();

            e57::CompressedVectorReader cReader = reader.SetUpData3DPointsData(0, BATCH, buffers);

            while (true) {
                size_t got = cReader.read();
                if (got == 0)
                    break;

                // Определяем коэффициент масштабирования для преобразования цвета.
                // Если максимальное значение красного канала в блоке > 255,
                // предполагаем, что значения в диапазоне 0..65535 и делим на 257,
                // иначе – значения уже в диапазоне 0..255.
                uint16_t max_r = 0;
                for (size_t i = 0; i < got; i++) {
                    if (rVals[i] > max_r)
                        max_r = rVals[i];
                }
                uint16_t scale = (max_r > 255 ? 257 : 1);

                for (size_t i = 0; i < got; i++) {
                    E57Point pt;
                    pt.x = xVals[i];
                    pt.y = yVals[i];
                    pt.z = zVals[i];

                    if (colorInvalid[i] != 0) {
                        // Если цвет недопустим, подставляем значение по умолчанию (128,128,128)
                        pt.r = 128;
                        pt.g = 128;
                        pt.b = 128;
                    }
                    else {
                        // Преобразуем цвет: если значения в 16-битном формате – делим на scale,
                        // иначе оставляем как есть.
                        pt.r = static_cast<uint8_t>(rVals[i] / scale);
                        pt.g = static_cast<uint8_t>(gVals[i] / scale);
                        pt.b = static_cast<uint8_t>(bVals[i] / scale);
                    }

                    g_points.push_back(pt);
                }

                if (got < BATCH)
                    break;
            }

            cReader.close();
            reader.Close();

            return static_cast<int>(g_points.size());
        }
        catch (std::exception& ex) {
            std::cerr << "[importE57] Exception: " << ex.what() << "\n";
            return -1;
        }
        catch (...) {
            std::cerr << "[importE57] Unknown error\n";
            return -1;
        }
    }

    bool getPointData(int index,
        double* x, double* y, double* z,
        uint8_t* r, uint8_t* g, uint8_t* b)
    {
        if (index < 0 || index >= static_cast<int>(g_points.size()))
            return false;

        const E57Point& pt = g_points[index];
        *x = pt.x;
        *y = pt.y;
        *z = pt.z;
        *r = pt.r;
        *g = pt.g;
        *b = pt.b;
        return true;
    }

} // extern "C"
