require 'fiddle'

module GaussianPoints
  module Hook
    def self.setup_dll
      return true if @dll_loaded

      GaussianPoints::NativePaths.prepend_to_path(GaussianPoints::NativePaths.pointcloud_hook_support_dirs)
      bridge_path = GaussianPoints::NativePaths.bridge_dll
      renderer_path = GaussianPoints::NativePaths.pointcloud_renderer_dll
      return false unless bridge_path && renderer_path

      GaussianPoints::NativePaths.prepend_to_path(File.dirname(bridge_path), File.dirname(renderer_path))
      puts "[PointCloud Bridge] bridge=#{bridge_path}"
      puts "[PointCloud Bridge] renderer=#{renderer_path}"
      @renderer_dll = Fiddle.dlopen(renderer_path)
      @dll = Fiddle.dlopen(bridge_path)
      @install_all_hooks = Fiddle::Function.new(
        @dll['InstallAllHooks'],
        [],
        Fiddle::TYPE_VOID
      )
      @set_pointcloud_data = Fiddle::Function.new(
        @dll['SetPointCloudData'],
        [Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
        Fiddle::TYPE_VOID
      )
      @dll_loaded = true
    rescue Fiddle::DLError => e
      puts "[PointCloud Bridge] DLL load error: #{e.message}"
      @dll_loaded = false
      false
    end

    def self.install_hooks
      return false unless setup_dll

      @install_all_hooks.call
      true
    end

    def self.set_pointcloud(data)
      return false unless setup_dll
      return false unless install_hooks

      count = data.size / 6
      packed = data.pack('d*')
      mem = Fiddle::Pointer.malloc(packed.bytesize)
      mem[0, packed.bytesize] = packed
      @set_pointcloud_data.call(mem, count)
      true
    end

    def self.clear_pointcloud
      return false unless setup_dll
      return false unless install_hooks

      @set_pointcloud_data.call(0, 0)
      true
    end
  end
end
