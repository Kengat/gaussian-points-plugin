#ifndef OCTREE_PROCESSOR_H
#define OCTREE_PROCESSOR_H

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

extern "C" {

	/// \brief Обрабатывает (оптимизирует) облако точек, уменьшая число точек при сохранении основных контуров.
	/// \param points_in Указатель на входной массив точек в формате [x, y, z, r, g, b, ...].
	/// \param count Число точек во входном массиве.
	/// \param out_count Указатель, куда будет записано число точек после оптимизации.
	/// \return Указатель на новый массив точек (формат аналогичный входному). Вызывающая сторона должна освободить память с помощью freePointCloud.
	EXPORT double* processPointCloud(const double* points_in, int count, int* out_count);

	/// \brief Освобождает память, выделенную функцией processPointCloud.
	/// \param points Указатель на массив, который необходимо освободить.
	EXPORT void freePointCloud(double* points);

} // extern "C"

#endif // OCTREE_PROCESSOR_H
