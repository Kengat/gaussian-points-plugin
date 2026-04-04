require 'fiddle'

module GaussianPoints
  module IO
    module E57FiddleImporter
      def self.setup_dll
        return true if @dll_loaded

        dll_path = GaussianPoints::NativePaths.e57_importer_dll
        return false unless dll_path && File.exist?(dll_path)

        GaussianPoints::NativePaths.prepend_to_path(
          GaussianPoints::PLUGIN_DIR,
          File.dirname(dll_path)
        )
        puts "[E57 Importer] dll=#{dll_path}"
        @dll = Fiddle.dlopen(dll_path)
        puts "E57 importer DLL loaded: #{dll_path}"
        @dll_loaded = true

        @import_e57 = Fiddle::Function.new(
          @dll['importE57'],
          [Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_INT
        )

        @get_e57_point_count = Fiddle::Function.new(
          @dll['GetE57PointCount'],
          [Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_INT
        )

        @start_import_e57_async = Fiddle::Function.new(
          @dll['StartImportE57Async'],
          [Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_INT
        )

        @get_import_e57_status = Fiddle::Function.new(
          @dll['GetImportE57Status'],
          [Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_VOID
        )

        @get_import_e57_error = Fiddle::Function.new(
          @dll['GetImportE57Error'],
          [Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
          Fiddle::TYPE_INT
        )

        @get_point_data = Fiddle::Function.new(
          @dll['getPointData'],
          [
            Fiddle::TYPE_INT,
            Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP,
            Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP
          ],
          Fiddle::TYPE_INT
        )
        true
      rescue Fiddle::DLError => e
        puts "[E57 Importer] DLL load error: #{e.message}"
        @dll_loaded = false
        false
      end

      def self.import_file(filename)
        return [] unless setup_dll

        c_str = (filename + "\0").force_encoding('ASCII-8BIT')
        ptr = Fiddle::Pointer.to_ptr(c_str)

        count = @import_e57.call(ptr)
        if count < 0
          UI.messagebox('importE57 failed. Check the Ruby console for details.')
          return []
        end
        if count == 0
          puts '[E57 Importer] 0 points'
          return []
        end

        result_points = []
        buf_x = Fiddle::Pointer.malloc(Fiddle::SIZEOF_DOUBLE)
        buf_y = Fiddle::Pointer.malloc(Fiddle::SIZEOF_DOUBLE)
        buf_z = Fiddle::Pointer.malloc(Fiddle::SIZEOF_DOUBLE)
        buf_r = Fiddle::Pointer.malloc(1)
        buf_g = Fiddle::Pointer.malloc(1)
        buf_b = Fiddle::Pointer.malloc(1)

        (0...count).each do |i|
          ret = @get_point_data.call(i, buf_x, buf_y, buf_z, buf_r, buf_g, buf_b)
          break if ret == 0

          x = buf_x[0, 8].unpack('d').first
          y = buf_y[0, 8].unpack('d').first
          z = buf_z[0, 8].unpack('d').first
          rr = buf_r[0].ord & 0xFF
          gg = buf_g[0].ord & 0xFF
          bb = buf_b[0].ord & 0xFF

          result_points << [Geom::Point3d.new(x, y, z), rr, gg, bb]
        end

        puts "[E57 Importer] imported #{result_points.size} points"
        result_points
      end

      def self.point_count(filename)
        return -1 unless setup_dll

        c_str = (filename + "\0").force_encoding('ASCII-8BIT')
        @get_e57_point_count.call(Fiddle::Pointer.to_ptr(c_str))
      end

      def self.start_async_import(filename)
        return false unless setup_dll

        c_str = (filename + "\0").force_encoding('ASCII-8BIT')
        @start_import_e57_async.call(Fiddle::Pointer.to_ptr(c_str)) != 0
      end

      def self.async_status
        return { state: 0, total_points: 0, processed_points: 0, result_count: 0 } unless setup_dll

        state_ptr = Fiddle::Pointer.malloc(Fiddle::SIZEOF_INT)
        total_ptr = Fiddle::Pointer.malloc(Fiddle::SIZEOF_INT)
        processed_ptr = Fiddle::Pointer.malloc(Fiddle::SIZEOF_INT)
        result_ptr = Fiddle::Pointer.malloc(Fiddle::SIZEOF_INT)
        @get_import_e57_status.call(state_ptr, total_ptr, processed_ptr, result_ptr)
        {
          state: state_ptr[0, Fiddle::SIZEOF_INT].unpack1('l'),
          total_points: total_ptr[0, Fiddle::SIZEOF_INT].unpack1('l'),
          processed_points: processed_ptr[0, Fiddle::SIZEOF_INT].unpack1('l'),
          result_count: result_ptr[0, Fiddle::SIZEOF_INT].unpack1('l')
        }
      end

      def self.last_error
        return '' unless setup_dll

        buffer_size = 1024
        buffer = Fiddle::Pointer.malloc(buffer_size)
        @get_import_e57_error.call(buffer, buffer_size)
        buffer.to_s.split("\0", 2).first.to_s
      end

      def self.fetch_points_chunk(start_index, count)
        return [] unless setup_dll
        return [] if count <= 0

        result_points = []
        buf_x = Fiddle::Pointer.malloc(Fiddle::SIZEOF_DOUBLE)
        buf_y = Fiddle::Pointer.malloc(Fiddle::SIZEOF_DOUBLE)
        buf_z = Fiddle::Pointer.malloc(Fiddle::SIZEOF_DOUBLE)
        buf_r = Fiddle::Pointer.malloc(1)
        buf_g = Fiddle::Pointer.malloc(1)
        buf_b = Fiddle::Pointer.malloc(1)

        finish_index = start_index + count
        index = start_index
        while index < finish_index
          ret = @get_point_data.call(index, buf_x, buf_y, buf_z, buf_r, buf_g, buf_b)
          break if ret == 0

          x = buf_x[0, 8].unpack1('d')
          y = buf_y[0, 8].unpack1('d')
          z = buf_z[0, 8].unpack1('d')
          rr = buf_r[0].ord & 0xFF
          gg = buf_g[0].ord & 0xFF
          bb = buf_b[0].ord & 0xFF
          result_points << [Geom::Point3d.new(x, y, z), rr, gg, bb]
          index += 1
        end

        result_points
      end
    end
  end
end
