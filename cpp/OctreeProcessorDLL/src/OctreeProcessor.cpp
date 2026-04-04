#include "OctreeProcessor.h"

#include <pcl/filters/voxel_grid.h>
#include <pcl/octree/octree_search.h>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>

#include <array>
#include <cstdint>
#include <vector>

extern "C" {

EXPORT double* processPointCloud(const double* points_in, int count, int* out_count) {
    typedef pcl::PointXYZ PointT;
    typedef pcl::PointCloud<PointT> PointCloudT;

    if (points_in == nullptr || count <= 0 || out_count == nullptr) {
        return nullptr;
    }

    PointCloudT::Ptr cloud(new PointCloudT());
    cloud->width = static_cast<std::uint32_t>(count);
    cloud->height = 1;
    cloud->points.resize(static_cast<size_t>(count));

    std::vector<std::array<double, 3>> original_colors(static_cast<size_t>(count));
    for (int i = 0; i < count; ++i) {
        PointT point;
        point.x = static_cast<float>(points_in[i * 6 + 0]);
        point.y = static_cast<float>(points_in[i * 6 + 1]);
        point.z = static_cast<float>(points_in[i * 6 + 2]);
        cloud->points[static_cast<size_t>(i)] = point;
        original_colors[static_cast<size_t>(i)] = {
            points_in[i * 6 + 3],
            points_in[i * 6 + 4],
            points_in[i * 6 + 5]
        };
    }

    PointCloudT::Ptr cloud_filtered(new PointCloudT());
    pcl::VoxelGrid<PointT> voxel_filter;
    voxel_filter.setInputCloud(cloud);
    constexpr float leaf_size = 0.01f;
    voxel_filter.setLeafSize(leaf_size, leaf_size, leaf_size);
    voxel_filter.filter(*cloud_filtered);

    pcl::octree::OctreePointCloudSearch<PointT> octree(leaf_size);
    octree.setInputCloud(cloud);
    octree.addPointsFromInputCloud();

    const int new_count = static_cast<int>(cloud_filtered->points.size());
    double* out_points = new double[static_cast<size_t>(new_count) * 6];
    for (int i = 0; i < new_count; ++i) {
        const PointT& filtered_point = cloud_filtered->points[static_cast<size_t>(i)];
        std::vector<int> indices;
        std::vector<float> sqr_dists;
        int nearest_index = -1;
        if (octree.nearestKSearch(filtered_point, 1, indices, sqr_dists) > 0) {
            nearest_index = indices[0];
        }

        out_points[i * 6 + 0] = filtered_point.x;
        out_points[i * 6 + 1] = filtered_point.y;
        out_points[i * 6 + 2] = filtered_point.z;
        if (nearest_index >= 0) {
            out_points[i * 6 + 3] = original_colors[static_cast<size_t>(nearest_index)][0];
            out_points[i * 6 + 4] = original_colors[static_cast<size_t>(nearest_index)][1];
            out_points[i * 6 + 5] = original_colors[static_cast<size_t>(nearest_index)][2];
        } else {
            out_points[i * 6 + 3] = 128.0;
            out_points[i * 6 + 4] = 128.0;
            out_points[i * 6 + 5] = 128.0;
        }
    }

    *out_count = new_count;
    return out_points;
}

EXPORT void freePointCloud(double* points) {
    delete[] points;
}

} // extern "C"
