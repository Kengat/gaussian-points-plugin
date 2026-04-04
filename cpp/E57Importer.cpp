// E57Importer.cpp

#include "E57Importer.h"
#include <E57SimpleReader.h>
#include <E57SimpleData.h>
#include <vector>
#include <string>
#include <iostream>
#include <stdexcept>
#include <algorithm>

struct RawPoint {
    double x, y, z;
    uint16_t r, g, b;
    bool colorValid;
};

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
            e57::ReaderOptions options;
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

            const size_t BATCH = 10000;

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

            // Первый проход: читаем все точки с сырыми uint16 цветами
            // и находим глобальный максимум по каждому каналу.
            std::vector<RawPoint> rawPoints;
            uint16_t globalMaxR = 0, globalMaxG = 0, globalMaxB = 0;

            e57::CompressedVectorReader cReader = reader.SetUpData3DPointsData(0, BATCH, buffers);
            while (true) {
                size_t got = cReader.read();
                if (got == 0)
                    break;

                for (size_t i = 0; i < got; i++) {
                    RawPoint rp;
                    rp.x = xVals[i];
                    rp.y = yVals[i];
                    rp.z = zVals[i];
                    rp.colorValid = (colorInvalid[i] == 0);
                    rp.r = rVals[i];
                    rp.g = gVals[i];
                    rp.b = bVals[i];

                    if (rp.colorValid) {
                        if (rp.r > globalMaxR) globalMaxR = rp.r;
                        if (rp.g > globalMaxG) globalMaxG = rp.g;
                        if (rp.b > globalMaxB) globalMaxB = rp.b;
                    }

                    rawPoints.push_back(rp);
                }

                if (got < BATCH)
                    break;
            }
            cReader.close();
            reader.Close();

            // Определяем масштаб по глобальному максимуму всего файла.
            uint16_t scale_r = (globalMaxR > 255) ? 257 : 1;
            uint16_t scale_g = (globalMaxG > 255) ? 257 : 1;
            uint16_t scale_b = (globalMaxB > 255) ? 257 : 1;

            std::cerr << "[importE57] Color scale: R=" << scale_r
                      << " G=" << scale_g << " B=" << scale_b
                      << " (globalMax R=" << globalMaxR
                      << " G=" << globalMaxG << " B=" << globalMaxB << ")\n";

            // Второй проход: конвертируем цвета с единым масштабом.
            g_points.reserve(rawPoints.size());
            for (const auto& rp : rawPoints) {
                E57Point pt;
                pt.x = rp.x;
                pt.y = rp.y;
                pt.z = rp.z;

                if (!rp.colorValid) {
                    pt.r = 128;
                    pt.g = 128;
                    pt.b = 128;
                } else {
                    pt.r = static_cast<uint8_t>(rp.r / scale_r);
                    pt.g = static_cast<uint8_t>(rp.g / scale_g);
                    pt.b = static_cast<uint8_t>(rp.b / scale_b);
                }

                g_points.push_back(pt);
            }

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
