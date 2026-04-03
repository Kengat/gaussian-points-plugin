module GaussianPoints
  module NativePaths
    def self.plugin_root
      GaussianPoints::PLUGIN_DIR
    end

    def self.first_existing(*paths)
      paths.flatten.compact.find { |path| File.exist?(path) }
    end

    def self.pointcloud_hook_dll
      first_existing(
        File.join(plugin_root, 'cpp', 'build', 'PointCloudHookDLL', 'x64', 'Release', 'PointCloudHookDLL.dll'),
        File.join(plugin_root, 'PointCloudHookDLL.dll')
      )
    end

    def self.bridge_dll
      first_existing(
        File.join(plugin_root, 'sandbox', 'cpp', 'build', 'SketchUpOverlayBridge', 'SketchUpOverlayBridge', 'x64', 'Release', 'SketchUpOverlayBridge.dll'),
        File.join(plugin_root, 'sandbox', 'SketchUpOverlayBridge.dll'),
        File.join(plugin_root, 'external', 'SketchUpOverlayBridge.dll')
      )
    end

    def self.pointcloud_hook_support_dirs
      [
        File.join(plugin_root, 'sandbox', 'runtime'),
        File.join(plugin_root, 'sandbox'),
        File.join(plugin_root, 'cpp', 'build', 'PointCloudHookDLL', 'x64', 'Release'),
        File.join(plugin_root, 'cpp', 'build', 'PointCloudRendererDLL', 'PointCloudRendererDLL', 'x64', 'Release'),
        File.join(plugin_root, 'cpp', 'build', 'PointCloudRendererDLL', 'x64', 'Release'),
        plugin_root
      ].select { |dir| Dir.exist?(dir) }
    end

    def self.pointcloud_renderer_dll
      first_existing(
        File.join(plugin_root, 'sandbox', 'runtime', 'PointCloudRendererDLL.dll'),
        File.join(plugin_root, 'cpp', 'build', 'PointCloudRendererDLL', 'PointCloudRendererDLL', 'x64', 'Release', 'PointCloudRendererDLL.dll'),
        File.join(plugin_root, 'sandbox', 'PointCloudRendererDLL.dll'),
        File.join(plugin_root, 'cpp', 'build', 'PointCloudRendererDLL', 'x64', 'Release', 'PointCloudRendererDLL.dll'),
        File.join(plugin_root, 'PointCloudRendererDLL.dll')
      )
    end

    def self.e57_importer_dll
      first_existing(
        File.join(plugin_root, 'E57ImporterDLL.dll'),
        File.join(plugin_root, 'cpp', 'build', 'E57ImporterDLL', 'x64', 'Release', 'E57ImporterDLL.dll')
      )
    end

    def self.octree_processor_dll
      first_existing(
        File.join(plugin_root, 'OctreeProcessorDLL.dll'),
        File.join(plugin_root, 'cpp', 'build', 'OctreeProcessorDLL', 'x64', 'Release', 'OctreeProcessorDLL.dll')
      )
    end

    def self.prepend_to_path(*dirs)
      normalized = dirs.flatten.compact.map { |dir| File.expand_path(dir) }.uniq
      return if normalized.empty?

      current = ENV.fetch('PATH', '').split(File::PATH_SEPARATOR)
      ENV['PATH'] = (normalized + current.reject { |dir| normalized.include?(dir) }).join(File::PATH_SEPARATOR)
    end
  end
end
