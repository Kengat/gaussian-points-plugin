// E57Importer.cpp

#include "E57Importer.h"
#include <E57SimpleReader.h>
#include <E57SimpleData.h>

#include <algorithm>
#include <atomic>
#include <cstring>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

struct RawPoint {
    double x, y, z;
    uint16_t r, g, b;
    bool colorValid;
};

struct E57Point {
    double x, y, z;
    uint8_t r, g, b;
};

std::vector<E57Point> g_points;
std::mutex g_pointsMutex;
std::mutex g_importMutex;
std::thread g_importThread;
std::atomic<int> g_importState{0};
std::atomic<int> g_importTotalPoints{0};
std::atomic<int> g_importProcessedPoints{0};
std::atomic<int> g_importResultCount{0};
std::string g_importError;

constexpr int kImportIdle = 0;
constexpr int kImportRunning = 1;
constexpr int kImportCompleted = 2;
constexpr int kImportFailed = 3;

void CleanupFinishedImportThread()
{
    if (g_importThread.joinable() && g_importState.load() != kImportRunning) {
        g_importThread.join();
    }
}

int ReadE57PointCountInternal(const char* filename, std::string* error_out)
{
    try {
        e57::ReaderOptions options;
        e57::Reader reader(filename, options);
        if (!reader.IsOpen()) {
            if (error_out) *error_out = "Can't open file";
            return -1;
        }

        int64_t data3DCount = reader.GetData3DCount();
        if (data3DCount < 1) {
            reader.Close();
            return 0;
        }

        e57::Data3D d3d;
        if (!reader.ReadData3D(0, d3d)) {
            reader.Close();
            if (error_out) *error_out = "ReadData3D(0) failed";
            return -1;
        }

        reader.Close();
        return static_cast<int>(d3d.pointCount);
    }
    catch (const std::exception& ex) {
        if (error_out) *error_out = ex.what();
        return -1;
    }
    catch (...) {
        if (error_out) *error_out = "Unknown error";
        return -1;
    }
}

int ImportE57Internal(const char* filename, std::vector<E57Point>* out_points,
                     std::atomic<int>* progress_processed,
                     std::atomic<int>* progress_total,
                     std::string* error_out)
{
    if (out_points == nullptr) {
        if (error_out) *error_out = "Output buffer is null";
        return -1;
    }

    out_points->clear();

    try {
        e57::ReaderOptions options;
        e57::Reader reader(filename, options);
        if (!reader.IsOpen()) {
            if (error_out) *error_out = "Can't open file";
            return -1;
        }

        int64_t data3DCount = reader.GetData3DCount();
        if (data3DCount < 1) {
            reader.Close();
            return 0;
        }

        e57::Data3D d3d;
        if (!reader.ReadData3D(0, d3d)) {
            reader.Close();
            if (error_out) *error_out = "ReadData3D(0) failed";
            return -1;
        }

        const int total_points = static_cast<int>(d3d.pointCount);
        if (progress_total) progress_total->store(total_points);
        if (progress_processed) progress_processed->store(0);

        const size_t BATCH = 10000;
        std::vector<double> xVals(BATCH), yVals(BATCH), zVals(BATCH);
        std::vector<uint16_t> rVals(BATCH), gVals(BATCH), bVals(BATCH);
        std::vector<int8_t> colorInvalid(BATCH, 0);

        e57::Data3DPointsDouble buffers;
        buffers.cartesianX = xVals.data();
        buffers.cartesianY = yVals.data();
        buffers.cartesianZ = zVals.data();
        buffers.colorRed = rVals.data();
        buffers.colorGreen = gVals.data();
        buffers.colorBlue = bVals.data();
        buffers.isColorInvalid = colorInvalid.data();

        std::vector<RawPoint> rawPoints;
        rawPoints.reserve(static_cast<size_t>(total_points > 0 ? total_points : 0));
        uint16_t globalMaxR = 0;
        uint16_t globalMaxG = 0;
        uint16_t globalMaxB = 0;

        e57::CompressedVectorReader cReader = reader.SetUpData3DPointsData(0, BATCH, buffers);
        int processed = 0;
        while (true) {
            size_t got = cReader.read();
            if (got == 0) {
                break;
            }

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

            processed += static_cast<int>(got);
            if (progress_processed) progress_processed->store(processed);

            if (got < BATCH) {
                break;
            }
        }
        cReader.close();
        reader.Close();

        uint16_t scale_r = (globalMaxR > 255) ? 257 : 1;
        uint16_t scale_g = (globalMaxG > 255) ? 257 : 1;
        uint16_t scale_b = (globalMaxB > 255) ? 257 : 1;

        out_points->reserve(rawPoints.size());
        for (const RawPoint& rp : rawPoints) {
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

            out_points->push_back(pt);
        }

        if (progress_processed) progress_processed->store(static_cast<int>(out_points->size()));
        return static_cast<int>(out_points->size());
    }
    catch (const std::exception& ex) {
        if (error_out) *error_out = ex.what();
        return -1;
    }
    catch (...) {
        if (error_out) *error_out = "Unknown error";
        return -1;
    }
}

} // namespace

extern "C" {

int GetE57PointCount(const char* filename)
{
    std::string error;
    return ReadE57PointCountInternal(filename, &error);
}

int importE57(const char* filename)
{
    std::vector<E57Point> imported_points;
    std::string error;
    const int count = ImportE57Internal(filename, &imported_points, nullptr, nullptr, &error);
    if (count < 0) {
        std::cerr << "[importE57] Exception: " << error << "\n";
        return -1;
    }

    std::lock_guard<std::mutex> lock(g_pointsMutex);
    g_points.swap(imported_points);
    return count;
}

int StartImportE57Async(const char* filename)
{
    if (filename == nullptr || filename[0] == '\0') {
        return 0;
    }

    std::lock_guard<std::mutex> lock(g_importMutex);
    CleanupFinishedImportThread();
    if (g_importState.load() == kImportRunning) {
        return 0;
    }

    const std::string filename_copy(filename);
    g_importError.clear();
    g_importTotalPoints.store(0);
    g_importProcessedPoints.store(0);
    g_importResultCount.store(0);
    g_importState.store(kImportRunning);

    g_importThread = std::thread([filename_copy]() {
        std::vector<E57Point> imported_points;
        std::string error;
        const int count = ImportE57Internal(
            filename_copy.c_str(),
            &imported_points,
            &g_importProcessedPoints,
            &g_importTotalPoints,
            &error);

        {
            std::lock_guard<std::mutex> points_lock(g_pointsMutex);
            if (count >= 0) {
                g_points.swap(imported_points);
            } else {
                g_points.clear();
            }
        }

        {
            std::lock_guard<std::mutex> import_lock(g_importMutex);
            g_importError = error;
            g_importResultCount.store(count >= 0 ? count : 0);
            g_importState.store(count >= 0 ? kImportCompleted : kImportFailed);
        }
    });

    return 1;
}

void GetImportE57Status(
    int* state,
    int* total_points,
    int* processed_points,
    int* result_count)
{
    CleanupFinishedImportThread();
    if (state) *state = g_importState.load();
    if (total_points) *total_points = g_importTotalPoints.load();
    if (processed_points) *processed_points = g_importProcessedPoints.load();
    if (result_count) *result_count = g_importResultCount.load();
}

int GetImportE57Error(char* buffer, int buffer_size)
{
    std::lock_guard<std::mutex> lock(g_importMutex);
    if (buffer == nullptr || buffer_size <= 0) {
        return static_cast<int>(g_importError.size());
    }

    const size_t copy_size = (std::min)(static_cast<size_t>(buffer_size - 1), g_importError.size());
    if (copy_size > 0) {
        std::memcpy(buffer, g_importError.data(), copy_size);
    }
    buffer[copy_size] = '\0';
    return static_cast<int>(copy_size);
}

bool getPointData(int index,
    double* x, double* y, double* z,
    uint8_t* r, uint8_t* g, uint8_t* b)
{
    std::lock_guard<std::mutex> lock(g_pointsMutex);
    if (index < 0 || index >= static_cast<int>(g_points.size()))
        return false;

    const E57Point& pt = g_points[static_cast<size_t>(index)];
    *x = pt.x;
    *y = pt.y;
    *z = pt.z;
    *r = pt.r;
    *g = pt.g;
    *b = pt.b;
    return true;
}

} // extern "C"
