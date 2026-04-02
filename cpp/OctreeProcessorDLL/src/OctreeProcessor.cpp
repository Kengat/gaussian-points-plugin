#include "OctreeProcessor.h"

// Включаем необходимые заголовки PCL
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>
#include <pcl/filters/voxel_grid.h>
#include <pcl/octree/octree_search.h>
#include <vector>
#include <Eigen/Core>

extern "C" {

    EXPORT double* processPointCloud(const double* points_in, int count, int* out_count) {
        // Определяем тип точки — используем pcl::PointXYZRGB (координаты + цвет)
        typedef pcl::PointXYZRGB PointT;
        typedef pcl::PointCloud<PointT> PointCloudT;

        // 1. Преобразуем входной массив в облако точек PCL
        PointCloudT::Ptr cloud(new PointCloudT());
        cloud->width = count;
        cloud->height = 1;
        cloud->points.resize(count);
        for (int i = 0; i < count; ++i) {
            PointT pt;
            pt.x = points_in[i * 6 + 0];
            pt.y = points_in[i * 6 + 1];
            pt.z = points_in[i * 6 + 2];
            pt.r = static_cast<uint8_t>(points_in[i * 6 + 3]);
            pt.g = static_cast<uint8_t>(points_in[i * 6 + 4]);
            pt.b = static_cast<uint8_t>(points_in[i * 6 + 5]);
            cloud->points[i] = pt;
        }

        // 2. Применяем воксельную фильтрацию для первоначального уменьшения количества точек.
        PointCloudT::Ptr cloud_filtered(new PointCloudT());
        pcl::VoxelGrid<PointT> voxel_filter;
        voxel_filter.setInputCloud(cloud);
        float leaf_size = 0.01f; // 1 см (настройте под нужное разрешение)
        voxel_filter.setLeafSize(leaf_size, leaf_size, leaf_size);
        voxel_filter.filter(*cloud_filtered);

        // 3. Строим октодерево на отфильтрованном облаке
        float resolution = leaf_size;
        pcl::octree::OctreePointCloudSearch<PointT> octree(resolution);
        octree.setInputCloud(cloud_filtered);
        octree.addPointsFromInputCloud();

        // Получаем центры занятых вокселей.
        // Важно: тип вектора должен быть pcl::PointXYZRGB (а не Eigen::Vector3f),
        // поскольку метод getOccupiedVoxelCenters ожидает именно такой тип.
        std::vector<PointT, Eigen::aligned_allocator<PointT>> voxel_centers;
        octree.getOccupiedVoxelCenters(voxel_centers);

        // 4. Для каждого центра вокселя выполняем поиск ближайшей точки
        PointCloudT::Ptr cloud_optimized(new PointCloudT());
        for (const auto& center : voxel_centers) {
            std::vector<int> indices;
            std::vector<float> sqr_dists;
            PointT searchPoint;
            searchPoint.x = center.x;
            searchPoint.y = center.y;
            searchPoint.z = center.z;
            if (octree.nearestKSearch(searchPoint, 1, indices, sqr_dists) > 0) {
                cloud_optimized->points.push_back(cloud_filtered->points[indices[0]]);
            }
        }
        cloud_optimized->width = static_cast<uint32_t>(cloud_optimized->points.size());
        cloud_optimized->height = 1;

        // 5. Преобразуем оптимизированное облако обратно в массив double.
        int new_count = static_cast<int>(cloud_optimized->points.size());
        double* out_points = new double[new_count * 6]; // 6 значений на точку: x,y,z,r,g,b
        for (int i = 0; i < new_count; i++) {
            PointT pt = cloud_optimized->points[i];
            out_points[i * 6 + 0] = pt.x;
            out_points[i * 6 + 1] = pt.y;
            out_points[i * 6 + 2] = pt.z;
            out_points[i * 6 + 3] = static_cast<double>(pt.r);
            out_points[i * 6 + 4] = static_cast<double>(pt.g);
            out_points[i * 6 + 5] = static_cast<double>(pt.b);
        }

        *out_count = new_count;
        return out_points;
    }

    EXPORT void freePointCloud(double* points) {
        delete[] points;
    }

} // extern "C"
