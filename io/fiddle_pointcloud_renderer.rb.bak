# io/fiddle_pointcloud_renderer.rb
require 'fiddle'
require 'fiddle/import'

module GaussianPoints
  module IO
    module PointCloudRenderer
      def self.setup_dll
        script_path = File.expand_path(__FILE__)
        plugin_dir  = File.dirname(script_path)
        dll_path    = File.join(plugin_dir, "..", "PointCloudRendererDLL.dll")
        unless File.exist?(dll_path)
          UI.messagebox("Не найден DLL: #{dll_path}")
          @dll_loaded = false
          return
        end
        @dll = Fiddle.dlopen(dll_path)
        @dll_loaded = true

        # Определяем сигнатуру функции renderPointCloud:
        # void renderPointCloud(const double* points_in, int count)
        @renderPointCloud = Fiddle::Function.new(
          @dll['renderPointCloud'],
          [Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
          Fiddle::TYPE_VOID
        )
      end

      setup_dll

      # Обёртка для вызова renderPointCloud.
      # points_array: массив точек [x, y, z, r, g, b, ...]
      # count: число точек (points_array.size / 6)
      def self.render(points_array, count)
        mem = Fiddle::Pointer.malloc(points_array.size * Fiddle::SIZEOF_DOUBLE)
        mem[0, points_array.size * Fiddle::SIZEOF_DOUBLE] = points_array.pack("d*")
        @renderPointCloud.call(mem, count)
      end
    end
  end
end
