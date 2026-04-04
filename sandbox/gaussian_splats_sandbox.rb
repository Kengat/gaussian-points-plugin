# gaussian_splats_sandbox.rb - Internal Gaussian splat runtime for GaussianPoints
require 'fiddle'
require 'fiddle/import'
require 'json'
require 'sketchup.rb'

module GaussianPoints
  module GaussianSplats
    PREF_NAMESPACE = 'GaussianPoints'.freeze
    PREF_UP_AXIS_KEY = 'gaussian_splats_up_axis'.freeze
    PREF_SH_RENDER_DEGREE_KEY = 'gaussian_splats_sh_render_degree'.freeze
    PREF_FAST_APPROXIMATE_SORTING_KEY = 'gaussian_splats_fast_approximate_sorting'.freeze
    ORIENTATION_LEGACY = 'legacy'.freeze
    ORIENTATION_SWAP_A = 'swap_a'.freeze
    ORIENTATION_SWAP_B = 'swap_b'.freeze
    ORIENTATION_OPTIONS = [
      ORIENTATION_LEGACY,
      ORIENTATION_SWAP_A,
      ORIENTATION_SWAP_B
    ].freeze

    def self.plugin_dir
      File.join(GaussianPoints::PLUGIN_DIR, 'sandbox')
    end

    def self.valid_dll_path?(path)
      path && File.file?(path) && File.size?(path)
    end

    def self.first_valid_dll(*paths)
      paths.flatten.compact.find { |path| valid_dll_path?(path) }
    end

    def self.normalize_up_axis_mode(value)
      normalized = value.to_s.downcase
      return ORIENTATION_LEGACY if normalized == 'z'
      return ORIENTATION_SWAP_A if normalized == 'y'
      return normalized if ORIENTATION_OPTIONS.include?(normalized)

      ORIENTATION_SWAP_B
    end

    def self.orientation_label(value)
      case normalize_up_axis_mode(value)
      when ORIENTATION_LEGACY
        '1. Y-up (original/Postshot)'
      when ORIENTATION_SWAP_A
        '2. Z-up inverted'
      when ORIENTATION_SWAP_B
        '3. Z-up (default)'
      else
        '3. Z-up (default)'
      end
    end

    def self.up_axis_mode
      normalize_up_axis_mode(Sketchup.read_default(PREF_NAMESPACE, PREF_UP_AXIS_KEY, ORIENTATION_SWAP_B))
    rescue StandardError
      ORIENTATION_SWAP_B
    end

    def self.set_up_axis_mode(value)
      normalized = normalize_up_axis_mode(value)
      Sketchup.write_default(PREF_NAMESPACE, PREF_UP_AXIS_KEY, normalized)
      normalized
    rescue StandardError
      ORIENTATION_SWAP_B
    end

    def self.up_axis_mode_native_value(value = up_axis_mode)
      case normalize_up_axis_mode(value)
      when ORIENTATION_SWAP_A
        1
      when ORIENTATION_SWAP_B
        2
      else
        0
      end
    end

    def self.normalize_sh_render_degree(value)
      [[value.to_i, 0].max, 3].min
    rescue StandardError
      3
    end

    def self.sh_render_degree
      normalize_sh_render_degree(Sketchup.read_default(PREF_NAMESPACE, PREF_SH_RENDER_DEGREE_KEY, 3))
    rescue StandardError
      3
    end

    def self.set_sh_render_degree(value)
      degree = normalize_sh_render_degree(value)
      Sketchup.write_default(PREF_NAMESPACE, PREF_SH_RENDER_DEGREE_KEY, degree)
      apply_sh_render_degree(degree)
      Sketchup.active_model&.active_view&.invalidate
      degree
    rescue StandardError
      3
    end

    def self.hook_dll_path
      GaussianPoints::NativePaths.bridge_dll
    end

    def self.fast_approximate_sorting_enabled?
      Sketchup.read_default(PREF_NAMESPACE, PREF_FAST_APPROXIMATE_SORTING_KEY, false) == true
    rescue StandardError
      false
    end

    def self.set_fast_approximate_sorting_enabled(value)
      enabled = (value == true || value.to_s == 'true' || value.to_s == '1')
      Sketchup.write_default(PREF_NAMESPACE, PREF_FAST_APPROXIMATE_SORTING_KEY, enabled)
      apply_fast_approximate_sorting(enabled)
      Sketchup.active_model&.active_view&.invalidate
      enabled
    rescue StandardError
      false
    end

    def self.renderer_dll_path
      first_valid_dll(
        File.join(plugin_dir, 'runtime', 'GaussianSplatRenderer.dll'),
        File.join(plugin_dir, 'GaussianSplatRenderer.dll'),
        File.join(plugin_dir, 'cpp', 'build', 'GaussianSplatRenderer', 'GaussianSplatRenderer', 'x64', 'Release', 'GaussianSplatRenderer.dll'),
        File.join(plugin_dir, 'build', 'gaussian', 'GaussianSplatRenderer.dll')
      )
    end

    def self.ply_importer_path
      first_valid_dll(
        File.join(plugin_dir, 'runtime', 'PlyImporter.dll'),
        File.join(plugin_dir, 'PlyImporter.dll'),
        File.join(plugin_dir, 'cpp', 'build', 'PlyImporter', 'PlyImporter', 'x64', 'Release', 'PlyImporter.dll')
      )
    end

    def self.to_c_string(value)
      Fiddle::Pointer[(value.encode('UTF-8') + "\0")]
    end

    def self.available?
      hook_path = hook_dll_path
      renderer_path = renderer_dll_path
      !hook_path.nil? && !renderer_path.nil? && valid_dll_path?(hook_path) && valid_dll_path?(renderer_path) && !ply_importer_path.nil?
    end

    def self.initialized?
      @initialized == true
    end

    def self.loaded?
      @hook_dll_loaded == true && @renderer_dll_loaded == true && @ply_dll_loaded == true
    end

    def self.status_payload
      native_sh_degree =
        begin
          if @renderer_dll_loaded && @get_sh_render_degree
            normalize_sh_render_degree(@get_sh_render_degree.call)
          else
            sh_render_degree
          end
        rescue StandardError
          sh_render_degree
        end

      {
        available: available?,
        initialized: initialized?,
        loaded: loaded?,
        up_axis_mode: up_axis_mode,
        sh_render_degree: native_sh_degree,
        fast_approximate_sorting: begin
          if @renderer_dll_loaded && @get_fast_approximate_sorting_enabled
            @get_fast_approximate_sorting_enabled.call != 0
          else
            fast_approximate_sorting_enabled?
          end
        rescue StandardError
          fast_approximate_sorting_enabled?
        end,
        sandbox_dir: plugin_dir,
        hook_loaded: @hook_dll_loaded == true,
        renderer_loaded: @renderer_dll_loaded == true,
        ply_loaded: @ply_dll_loaded == true,
        hook_path: hook_dll_path,
        renderer_path: renderer_dll_path,
        ply_path: ply_importer_path
      }
    end

    def self.setup_dlls
      return true if @setup_complete

      hook_path = hook_dll_path
      renderer_path = renderer_dll_path
      ply_dll_path = ply_importer_path
      extra_paths = [
        plugin_dir,
        File.join(plugin_dir, 'runtime'),
        hook_path && File.dirname(hook_path),
        renderer_path && File.dirname(renderer_path),
        ply_dll_path && File.dirname(ply_dll_path)
      ].compact.uniq
      path_entries = ENV.fetch('PATH', '').split(File::PATH_SEPARATOR)
      ENV['PATH'] = (extra_paths + path_entries.reject { |entry| extra_paths.include?(entry) }).join(File::PATH_SEPARATOR)
      @support_dlls = []

      GaussianPoints::NativePaths.pointcloud_hook_support_dirs.each do |support_dir|
        next unless Dir.exist?(support_dir)

        ENV['PATH'] = ([support_dir] + ENV.fetch('PATH', '').split(File::PATH_SEPARATOR).reject { |entry| entry == support_dir }).join(File::PATH_SEPARATOR)
      end

      %w[glew32.dll minhook.x64.dll].each do |dll_name|
        dll_path = first_valid_dll(
          File.join(plugin_dir, dll_name),
          File.join(plugin_dir, 'runtime', dll_name)
        )
        next unless File.exist?(dll_path)

        @support_dlls << Fiddle.dlopen(dll_path)
      end

      if hook_path && File.exist?(hook_path)
        @hook_dll = Fiddle.dlopen(hook_path)
        @hook_dll_loaded = true
        puts "[GaussianSplats] hook=#{hook_path}"
      else
        @hook_dll_loaded = false
      end

      if renderer_path && File.exist?(renderer_path)
        @renderer_dll = Fiddle.dlopen(renderer_path)
        @renderer_dll_loaded = true
        puts "[GaussianSplats] renderer=#{renderer_path}"
      else
        @renderer_dll_loaded = false
      end

      if ply_dll_path && File.exist?(ply_dll_path)
        @ply_dll = Fiddle.dlopen(ply_dll_path)
        @ply_dll_loaded = true
        puts "[GaussianSplats] ply=#{ply_dll_path}"
      else
        @ply_dll_loaded = false
      end

      if @hook_dll_loaded
        @install_all_hooks = Fiddle::Function.new(
          @hook_dll['InstallAllHooks'],
          [],
          Fiddle::TYPE_VOID
        )
      end

      if @renderer_dll_loaded
        @render_point_cloud = Fiddle::Function.new(
          @renderer_dll['renderPointCloud'],
          [],
          Fiddle::TYPE_VOID
        )

        @clear_splats = Fiddle::Function.new(
          @renderer_dll['ClearSplats'],
          [],
          Fiddle::TYPE_VOID
        )

        @load_splats_from_ply = Fiddle::Function.new(
          @renderer_dll['LoadSplatsFromPLY'],
          [Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_VOID
        )
        begin
          @load_splats_from_ply_with_up_axis = Fiddle::Function.new(
            @renderer_dll['LoadSplatsFromPLYWithUpAxis'],
            [Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
            Fiddle::TYPE_VOID
          )
        rescue Fiddle::DLError
          @load_splats_from_ply_with_up_axis = nil
        end
        begin
          @load_splat_object_from_ply = Fiddle::Function.new(
            @renderer_dll['LoadSplatObjectFromPLY'],
            [Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP],
            Fiddle::TYPE_INT
          )
          begin
            @load_splat_object_from_ply_with_up_axis = Fiddle::Function.new(
              @renderer_dll['LoadSplatObjectFromPLYWithUpAxis'],
              [Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
              Fiddle::TYPE_INT
            )
          rescue Fiddle::DLError
            @load_splat_object_from_ply_with_up_axis = nil
          end
          @set_splat_object_transform = Fiddle::Function.new(
            @renderer_dll['SetSplatObjectTransform'],
            [Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
            Fiddle::TYPE_INT
          )
          @set_splat_object_highlight = Fiddle::Function.new(
            @renderer_dll['SetSplatObjectHighlight'],
            [Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
            Fiddle::TYPE_INT
          )
          @remove_splat_object = Fiddle::Function.new(
            @renderer_dll['RemoveSplatObject'],
            [Fiddle::TYPE_VOIDP],
            Fiddle::TYPE_INT
          )
          @clear_splat_objects = Fiddle::Function.new(
            @renderer_dll['ClearSplatObjects'],
            [],
            Fiddle::TYPE_VOID
          )
        rescue Fiddle::DLError
          @load_splat_object_from_ply = nil
          @load_splat_object_from_ply_with_up_axis = nil
          @set_splat_object_transform = nil
          @set_splat_object_highlight = nil
          @remove_splat_object = nil
          @clear_splat_objects = nil
        end

        begin
          @set_sh_render_degree = Fiddle::Function.new(
            @renderer_dll['SetSHRenderDegree'],
            [Fiddle::TYPE_INT],
            Fiddle::TYPE_VOID
          )
        rescue Fiddle::DLError
          @set_sh_render_degree = nil
        end

        begin
          @get_sh_render_degree = Fiddle::Function.new(
            @renderer_dll['GetSHRenderDegree'],
            [],
            Fiddle::TYPE_INT
          )
        rescue Fiddle::DLError
          @get_sh_render_degree = nil
        end

        begin
          @set_fast_approximate_sorting_enabled = Fiddle::Function.new(
            @renderer_dll['SetFastApproximateSortingEnabled'],
            [Fiddle::TYPE_INT],
            Fiddle::TYPE_VOID
          )
        rescue Fiddle::DLError
          @set_fast_approximate_sorting_enabled = nil
        end

        begin
          @get_fast_approximate_sorting_enabled = Fiddle::Function.new(
            @renderer_dll['GetFastApproximateSortingEnabled'],
            [],
            Fiddle::TYPE_INT
          )
        rescue Fiddle::DLError
          @get_fast_approximate_sorting_enabled = nil
        end

        begin
          @get_splat_bounds = Fiddle::Function.new(
            @renderer_dll['GetSplatBounds'],
            [Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP],
            Fiddle::TYPE_INT
          )
        rescue Fiddle::DLError
          @get_splat_bounds = nil
        end
      end

      if @ply_dll_loaded
        @load_ply_file = Fiddle::Function.new(
          @ply_dll['LoadPLYFile'],
          [Fiddle::TYPE_VOIDP],
          Fiddle::TYPE_INT
        )
      end

      @setup_complete = true
      true
    rescue StandardError => e
      puts "[GaussianSplats] setup error: #{e.class}: #{e.message}"
      @setup_complete = false
      @hook_dll_loaded = false
      @renderer_dll_loaded = false
      @ply_dll_loaded = false
      false
    end

    def self.ensure_initialized
      return false unless setup_dlls
      return false unless @hook_dll_loaded
      return true if initialized?

      @install_all_hooks.call
      @initialized = true
      apply_sh_render_degree(sh_render_degree)
      apply_fast_approximate_sorting(fast_approximate_sorting_enabled?)
      puts 'Gaussian splat hooks installed'
      true
    rescue StandardError => e
      puts "[GaussianSplats] init error: #{e.message}"
      false
    end

    def self.render_splats
      return false unless ensure_initialized
      return false unless @renderer_dll_loaded

      @render_point_cloud.call
      true
    end

    def self.clear_splats
      return false unless @renderer_dll_loaded

      if @clear_splat_objects
        @clear_splat_objects.call
      else
        @clear_splats.call
      end
      GaussianPoints::SceneBoundsProxy.clear_splats if defined?(GaussianPoints::SceneBoundsProxy)
      true
    end

    def self.analyze_ply
      return false unless setup_dlls
      return false unless @ply_dll_loaded

      filename = UI.openpanel('Choose a PLY file', '', 'PLY Files|*.ply||')
      return false if filename.nil? || filename.empty?

      analyze_ply_file(filename)
    end

    def self.analyze_ply_file(filename)
      return false unless setup_dlls
      return false unless @ply_dll_loaded

      c_filename = to_c_string(filename.tr('/', '\\'))
      @load_ply_file.call(c_filename) != 0
    end

    def self.load_ply_splats
      return false unless ensure_initialized
      return false unless @renderer_dll_loaded

      filename = UI.openpanel('Choose a Gaussian PLY file', '', 'PLY Files|*.ply||')
      return false if filename.nil? || filename.empty?

      load_ply_splats_file(filename)
    end

    def self.load_ply_splats_file(filename, up_axis_mode: self.up_axis_mode)
      return false unless ensure_initialized
      return false unless @renderer_dll_loaded

      c_filename = to_c_string(filename.tr('/', '\\'))
      if @load_splats_from_ply_with_up_axis
        @load_splats_from_ply_with_up_axis.call(c_filename, up_axis_mode_native_value(up_axis_mode))
      else
        @load_splats_from_ply.call(c_filename)
      end
      sync_bounds_proxy_from_renderer
      true
    end

    def self.load_ply_splats_file_as_object(id, filename, up_axis_mode: self.up_axis_mode)
      return nil unless ensure_initialized
      return nil unless @renderer_dll_loaded
      return nil unless @load_splat_object_from_ply

      center_buffer = Fiddle::Pointer.malloc(3 * Fiddle::SIZEOF_DOUBLE)
      half_buffer = Fiddle::Pointer.malloc(3 * Fiddle::SIZEOF_DOUBLE)
      center_buffer[0, 3 * Fiddle::SIZEOF_DOUBLE] = [0.0, 0.0, 0.0].pack('d3')
      half_buffer[0, 3 * Fiddle::SIZEOF_DOUBLE] = [0.0, 0.0, 0.0].pack('d3')

      result =
        if @load_splat_object_from_ply_with_up_axis
          @load_splat_object_from_ply_with_up_axis.call(
            to_c_string(id),
            to_c_string(filename.tr('/', '\\')),
            center_buffer,
            half_buffer,
            up_axis_mode_native_value(up_axis_mode)
          )
        else
          @load_splat_object_from_ply.call(
            to_c_string(id),
            to_c_string(filename.tr('/', '\\')),
            center_buffer,
            half_buffer
          )
        end
      return nil if result == 0

      center = center_buffer[0, 3 * Fiddle::SIZEOF_DOUBLE].unpack('d3')
      half_extents = half_buffer[0, 3 * Fiddle::SIZEOF_DOUBLE].unpack('d3')

      {
        center: Geom::Point3d.new(*center),
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

    def self.set_object_transform(id, snapshot, visible: true)
      return false unless ensure_initialized
      return false unless @renderer_dll_loaded
      return false unless @set_splat_object_transform

      @set_splat_object_transform.call(
        to_c_string(id),
        pack_point(snapshot[:center]),
        pack_half_extents(snapshot[:half_extents]),
        pack_axes(snapshot[:axes]),
        visible ? 1 : 0
      ) != 0
    end

    def self.supports_highlight_api?
      setup_dlls && !@set_splat_object_highlight.nil?
    end

    def self.set_object_highlight(id, highlight_mode)
      return false unless ensure_initialized
      return false unless @renderer_dll_loaded
      return false unless @set_splat_object_highlight

      @set_splat_object_highlight.call(to_c_string(id), highlight_mode.to_i) != 0
    end

    def self.remove_object(id)
      return false unless ensure_initialized
      return false unless @renderer_dll_loaded
      return false unless @remove_splat_object

      @remove_splat_object.call(to_c_string(id)) != 0
    end

    def self.clear_splat_objects
      clear_splats
    end

    def self.apply_sh_render_degree(degree = sh_render_degree)
      return false unless setup_dlls
      return false unless @renderer_dll_loaded
      return false unless @set_sh_render_degree

      @set_sh_render_degree.call(normalize_sh_render_degree(degree))
      true
    end

    def self.apply_fast_approximate_sorting(enabled = fast_approximate_sorting_enabled?)
      return false unless setup_dlls
      return false unless @renderer_dll_loaded
      return false unless @set_fast_approximate_sorting_enabled

      @set_fast_approximate_sorting_enabled.call(enabled ? 1 : 0)
      true
    end

    def self.init_plugin
      ensure_initialized
    end

    def self.stop_plugin
      clear_splats
    end

    def self.sync_bounds_proxy_from_renderer
      return unless defined?(GaussianPoints::SceneBoundsProxy)
      return unless @get_splat_bounds

      min_buffer = Fiddle::Pointer.malloc(3 * Fiddle::SIZEOF_DOUBLE)
      max_buffer = Fiddle::Pointer.malloc(3 * Fiddle::SIZEOF_DOUBLE)
      min_buffer[0, 3 * Fiddle::SIZEOF_DOUBLE] = [0.0, 0.0, 0.0].pack('d*')
      max_buffer[0, 3 * Fiddle::SIZEOF_DOUBLE] = [0.0, 0.0, 0.0].pack('d*')

      result = @get_splat_bounds.call(min_buffer, max_buffer)
      return unless result != 0

      min_point = min_buffer[0, 3 * Fiddle::SIZEOF_DOUBLE].unpack('d3')
      max_point = max_buffer[0, 3 * Fiddle::SIZEOF_DOUBLE].unpack('d3')
      GaussianPoints::SceneBoundsProxy.update_splats_bounds(min_point, max_point)
    rescue StandardError => e
      puts "[GaussianSplats] bounds sync error: #{e.message}"
    end

    def self.pack_point(point)
      pack_doubles(point ? [point.x.to_f, point.y.to_f, point.z.to_f] : [0.0, 0.0, 0.0])
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
