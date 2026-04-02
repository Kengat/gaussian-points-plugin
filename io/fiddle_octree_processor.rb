# io/fiddle_octree_processor.rb
require 'fiddle'
require 'fiddle/import'

module GaussianPoints
  module IO
    module OctreeProcessor
      def self.setup_dll
        script_path = File.expand_path(__FILE__)
        plugin_dir  = File.dirname(script_path)
        dll_path    = File.join(plugin_dir, "..", "OctreeProcessorDLL.dll")
        unless File.exist?(dll_path)
          UI.messagebox("Не найден DLL: #{dll_path}")
          @dll_loaded = false
          return
        end
        @dll = Fiddle.dlopen(dll_path)
        @dll_loaded = true

        # Сигнатура функции processPointCloud:
        # double* processPointCloud(const double* points_in, int count, int* out_count)
        @processPointCloud = Fiddle::Function.new(
          @dll['processPointCloud'],
          [Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT, Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_VOIDP
        )

        # Сигнатура функции freePointCloud:
        # void freePointCloud(double* points)
        @freePointCloud = Fiddle::Function.new(
          @dll['freePointCloud'],
          [Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_VOID
        )
      end

      setup_dll

      # Обёртка для вызова processPointCloud.
      # Принимает массив чисел [x,y,z,r,g,b,...] и возвращает оптимизированный массив в том же формате.
      def self.process(points_array)
        count = points_array.size / 6
        mem = Fiddle::Pointer.malloc(points_array.size * Fiddle::SIZEOF_DOUBLE)
        mem[0, points_array.size * Fiddle::SIZEOF_DOUBLE] = points_array.pack("d*")
        out_count_ptr = Fiddle::Pointer.malloc(Fiddle::SIZEOF_INT)
        result_ptr = @processPointCloud.call(mem, count, out_count_ptr)
        new_count = out_count_ptr[0, Fiddle::SIZEOF_INT].unpack("i").first
        result = result_ptr[0, new_count * 6 * Fiddle::SIZEOF_DOUBLE].unpack("d*")
        @freePointCloud.call(result_ptr)
        result
      end
    end
  end
end
