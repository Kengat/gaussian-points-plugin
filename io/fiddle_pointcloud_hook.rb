# io/fiddle_hook.rb
require 'fiddle'
require 'fiddle/import'

module GaussianPoints
  module Hook
    extend Fiddle::Importer

    # Предполагается, что PointCloudHookDLL.dll лежит рядом
    dll_path = File.join(File.dirname(__FILE__), "..", "PointCloudHookDLL.dll")
    dlload dll_path

    extern 'void InstallAllHooks()'
    extern 'void SetPointCloudData(const double*, int)'

    # Вызываем установку хуков
    def self.install_hooks
      InstallAllHooks()
    end

    # Передаём массив [x,y,z,r,g,b,...] в Hook-DLL -> Renderer-DLL
    def self.set_pointcloud(data)
      count = data.size / 6
      mem = Fiddle::Pointer.malloc(data.size * Fiddle::SIZEOF_DOUBLE)
      mem[0, data.size * Fiddle::SIZEOF_DOUBLE] = data.pack("d*")
      SetPointCloudData(mem, count)
    end
  end
end

# При загрузке
GaussianPoints::Hook.install_hooks
