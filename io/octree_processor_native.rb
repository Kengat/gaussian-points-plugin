require 'fiddle'

module GaussianPoints
  module IO
    module OctreeProcessor
      def self.setup_dll
        return false if @disabled
        return true if @dll_loaded

        dll_path = GaussianPoints::NativePaths.octree_processor_dll
        return false unless dll_path && File.exist?(dll_path)

        GaussianPoints::NativePaths.prepend_to_path(
          GaussianPoints::PLUGIN_DIR,
          File.dirname(dll_path)
        )
        puts "[OctreeProcessor] dll=#{dll_path}"
        @dll = Fiddle.dlopen(dll_path)
        @dll_loaded = true

        @process_point_cloud = Fiddle::Function.new(
          @dll['processPointCloud'],
          [Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT, Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_VOIDP
        )

        @free_point_cloud = Fiddle::Function.new(
          @dll['freePointCloud'],
          [Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_VOID
        )
        true
      rescue Fiddle::DLError => e
        puts "[OctreeProcessor] native optimization unavailable: #{e.message}"
        @dll_loaded = false
        @disabled = true
        false
      end

      def self.process(points_array)
        return points_array unless setup_dll

        count = points_array.size / 6
        packed = points_array.pack('d*')
        mem = Fiddle::Pointer.malloc(packed.bytesize)
        mem[0, packed.bytesize] = packed

        out_count_ptr = Fiddle::Pointer.malloc(Fiddle::SIZEOF_INT)
        result_ptr = @process_point_cloud.call(mem, count, out_count_ptr)
        return points_array if result_ptr.null?

        new_count = out_count_ptr[0, Fiddle::SIZEOF_INT].unpack('i').first
        result = result_ptr[0, new_count * 6 * Fiddle::SIZEOF_DOUBLE].unpack('d*')
        @free_point_cloud.call(result_ptr)
        result
      end
    end
  end
end
