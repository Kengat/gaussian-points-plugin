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
      begin
        @set_pointcloud_object_data = Fiddle::Function.new(
          @renderer_dll['SetPointCloudObjectData'],
          [Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
          Fiddle::TYPE_INT
        )
        @load_pointcloud_object_from_gasp = Fiddle::Function.new(
          @renderer_dll['LoadPointCloudObjectFromGasp'],
          [Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_INT
        )
        @set_pointcloud_object_transform = Fiddle::Function.new(
          @renderer_dll['SetPointCloudObjectTransform'],
          [Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
          Fiddle::TYPE_INT
        )
        @set_pointcloud_object_highlight = Fiddle::Function.new(
          @renderer_dll['SetPointCloudObjectHighlight'],
          [Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
          Fiddle::TYPE_INT
        )
        @remove_pointcloud_object = Fiddle::Function.new(
          @renderer_dll['RemovePointCloudObject'],
          [Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_INT
        )
        @clear_pointcloud_objects = Fiddle::Function.new(
          @renderer_dll['ClearPointCloudObjects'],
          [],
          Fiddle::TYPE_VOID
        )
      rescue Fiddle::DLError
        @set_pointcloud_object_data = nil
        @load_pointcloud_object_from_gasp = nil
        @set_pointcloud_object_transform = nil
        @set_pointcloud_object_highlight = nil
        @remove_pointcloud_object = nil
        @clear_pointcloud_objects = nil
      end
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

    def self.supports_object_api?
      setup_dll && !@set_pointcloud_object_data.nil? && !@set_pointcloud_object_transform.nil?
    end

    def self.supports_gasp_api?
      setup_dll && !@load_pointcloud_object_from_gasp.nil?
    end

    def self.supports_highlight_api?
      setup_dll && !@set_pointcloud_object_highlight.nil?
    end

    def self.upsert_pointcloud_object(id, data)
      return false unless setup_dll
      return false unless install_hooks
      return false unless supports_object_api?

      packed = data.pack('d*')
      mem = Fiddle::Pointer.malloc(packed.bytesize)
      mem[0, packed.bytesize] = packed
      @set_pointcloud_object_data.call(c_string(id), mem, data.size / 6) != 0
    end

    def self.set_pointcloud_object_transform(id, snapshot, visible: true)
      return false unless setup_dll
      return false unless install_hooks
      return false unless supports_object_api?

      @set_pointcloud_object_transform.call(
        c_string(id),
        pack_point(snapshot[:center]),
        pack_half_extents(snapshot[:half_extents]),
        pack_axes(snapshot[:axes]),
        visible ? 1 : 0
      ) != 0
    end

    def self.load_pointcloud_object_from_gasp(id, filename)
      return nil unless setup_dll
      return nil unless install_hooks
      return nil unless supports_gasp_api?

      center_ptr = pack_doubles([0.0, 0.0, 0.0])
      half_extents_ptr = pack_doubles([0.0, 0.0, 0.0])
      result = @load_pointcloud_object_from_gasp.call(
        c_string(id),
        c_string(filename),
        center_ptr,
        half_extents_ptr
      )
      return nil if result == 0

      center = center_ptr[0, 24].unpack('d3')
      half_extents = half_extents_ptr[0, 24].unpack('d3')
      {
        center: Geom::Point3d.new(center[0], center[1], center[2]),
        half_extents: {
          x: half_extents[0],
          y: half_extents[1],
          z: half_extents[2]
        },
        axes: {
          x: Geom::Vector3d.new(1, 0, 0),
          y: Geom::Vector3d.new(0, 1, 0),
          z: Geom::Vector3d.new(0, 0, 1)
        }
      }
    end

    def self.remove_pointcloud_object(id)
      return false unless setup_dll
      return false unless install_hooks
      return false unless @remove_pointcloud_object

      @remove_pointcloud_object.call(c_string(id)) != 0
    end

    def self.set_pointcloud_object_highlight(id, highlight_mode)
      return false unless setup_dll
      return false unless install_hooks
      return false unless @set_pointcloud_object_highlight

      @set_pointcloud_object_highlight.call(c_string(id), highlight_mode.to_i) != 0
    end

    def self.clear_pointcloud_objects
      return false unless setup_dll
      return false unless install_hooks
      return clear_pointcloud unless @clear_pointcloud_objects

      @clear_pointcloud_objects.call
      true
    end

    def self.c_string(value)
      Fiddle::Pointer[(value.to_s.encode('UTF-8') + "\0")]
    end

    def self.pack_point(point)
      values = point ? [point.x.to_f, point.y.to_f, point.z.to_f] : [0.0, 0.0, 0.0]
      pack_doubles(values)
    end

    def self.pack_half_extents(half_extents)
      values =
        if half_extents
          [half_extents[:x].to_f, half_extents[:y].to_f, half_extents[:z].to_f]
        else
          [0.0, 0.0, 0.0]
        end
      pack_doubles(values)
    end

    def self.pack_axes(axes)
      values =
        if axes
          %i[x y z].flat_map do |axis|
            vector = axes[axis]
            vector ? [vector.x.to_f, vector.y.to_f, vector.z.to_f] : [0.0, 0.0, 0.0]
          end
        else
          [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        end
      pack_doubles(values)
    end

    def self.pack_doubles(values)
      packed = values.pack("d#{values.length}")
      memory = Fiddle::Pointer.malloc(packed.bytesize)
      memory[0, packed.bytesize] = packed
      memory
    end
  end
end
