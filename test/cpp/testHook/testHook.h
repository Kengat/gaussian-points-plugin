#ifndef POINTCLOUDHOOK_H
#define POINTCLOUDHOOK_H

#ifdef _WIN32
#define PCH_API __declspec(dllexport)
#else
#define PCH_API
#endif

extern "C" {
	PCH_API void InstallAllHooks();
	PCH_API void SetPointCloudData(const double* points, int count);
}

#endif // POINTCLOUDHOOK_HK_H