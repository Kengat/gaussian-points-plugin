module GaussianPoints
  module IO
    module Importer
      def self.import_dialog
        filters = 'GASP|*.gasp||XYZ|*.xyz||E57|*.e57||All|*.*||'
        filename = UI.openpanel('Select file', '', filters)
        return unless filename

        ext = File.extname(filename).downcase
        case ext
        when '.gasp'
          import_gasp(filename)
        when '.xyz'
          import_xyz(filename)
        when '.e57'
          import_e57(filename)
        else
          UI.messagebox("Unknown file format: #{ext}")
        end
      end

      def self.import_xyz(filename = nil)
        unless filename
          filename = UI.openpanel('Select .xyz', '', 'XYZ|*.xyz||')
          return unless filename
        end
        if try_import_fresh_gasp_cache(filename)
          return
        end

        flat_data = read_xyz_flat_data(filename)
        if flat_data.empty?
          UI.messagebox('No points were imported from the XYZ file.')
          return
        end

        if GaussianPoints::Hook.supports_object_api?
          item = register_pointcloud_flat_data(File.basename(filename), flat_data, source_path: filename)
          if item
            UI.messagebox("Imported #{flat_data.length / 6} points from #{File.basename(filename)}")
          else
            UI.messagebox('Point cloud import failed.')
          end
        else
          overlay_points = []
          count = flat_data.length / 6
          count.times do |index|
            base = index * 6
            overlay_points << Geom::Point3d.new(flat_data[base + 0], flat_data[base + 1], flat_data[base + 2])
          end
          overlay = GaussianPoints.overlay
          if overlay
            overlay.add_points(overlay_points)
            UI.messagebox("Imported #{overlay_points.size} points from #{File.basename(filename)}")
          else
            UI.messagebox('Overlay not found.')
          end
        end
      end

      def self.import_e57(filename = nil)
        unless filename
          filename = UI.openpanel('Select .e57', '', 'E57|*.e57||')
          return unless filename
        end
        if try_import_fresh_gasp_cache(filename)
          return
        end

        colored_data = E57FiddleImporter.import_file(filename)
        if colored_data.empty?
          UI.messagebox('No points were imported from the E57 file.')
          return
        end

        puts "[E57 Import] Total points: #{colored_data.size}"
        puts '[E57 Import] First 3 points (xyz + rgb):'
        colored_data.first(3).each_with_index do |arr, i|
          pt, rr, gg, bb = arr
          rr ||= 128
          gg ||= 128
          bb ||= 128
          puts format('  %<index>d: (%<x>.3f, %<y>.3f, %<z>.3f), color=(%<r>d,%<g>d,%<b>d)',
                      index: i, x: pt.x, y: pt.y, z: pt.z, r: rr, g: gg, b: bb)
        end

        scale_factor = 39.3700787
        optimized_count = colored_data.size
        optimized_array = Array.new(optimized_count * 6)
        colored_data.each_with_index do |(pt, r, g, b), index|
          base = index * 6
          optimized_array[base + 0] = pt.x * scale_factor
          optimized_array[base + 1] = pt.y * scale_factor
          optimized_array[base + 2] = pt.z * scale_factor
          optimized_array[base + 3] = r.to_f / 255.0
          optimized_array[base + 4] = g.to_f / 255.0
          optimized_array[base + 5] = b.to_f / 255.0
        end
        puts "[Point Cloud] Prepared #{optimized_count} points for render/cache."
        GaussianPoints::SceneBoundsProxy.update_pointcloud_flat_array(optimized_array, stride: 6) if defined?(GaussianPoints::SceneBoundsProxy)

        if defined?(GaussianPoints::Hook) && GaussianPoints::Hook.supports_object_api?
          item = register_pointcloud_flat_data(File.basename(filename), optimized_array, source_path: filename)
          if item
            UI.messagebox("Imported #{optimized_count} points from #{File.basename(filename)}.")
          else
            UI.messagebox('Point cloud import failed.')
          end
        else
          optimized_data = []
          (0...optimized_count).each do |i|
            x = optimized_array[i * 6 + 0]
            y = optimized_array[i * 6 + 1]
            z = optimized_array[i * 6 + 2]
            r = optimized_array[i * 6 + 3].to_i
            g = optimized_array[i * 6 + 4].to_i
            b = optimized_array[i * 6 + 5].to_i
            optimized_data << [Geom::Point3d.new(x, y, z), r, g, b]
          end

          overlay = GaussianPoints.overlay
          if overlay
            overlay.add_colored_points(optimized_data)
            UI.messagebox("Imported #{optimized_count} points from #{File.basename(filename)}.")
          else
            UI.messagebox('Overlay not found.')
          end
        end
      end

      def self.import_gasp(filename = nil, display_name: nil, source_path: nil)
        unless filename
          filename = UI.openpanel('Select .gasp', '', 'GASP|*.gasp||')
          return unless filename
        end
        unless defined?(GaussianPoints::Hook) &&
               GaussianPoints::Hook.respond_to?(:supports_gasp_api?) &&
               GaussianPoints::Hook.supports_gasp_api?
          UI.messagebox('Native GASP loading is not available in the current build.')
          return nil
        end

        item = GaussianPoints::UIparts::RenderItemRegistry.register_pointcloud_project(
          name: display_name || File.basename(filename, '.*'),
          project_path: filename,
          source_path: source_path || filename
        )
        if item
          UI.messagebox("Loaded #{File.basename(filename)}")
        else
          UI.messagebox('GASP project load failed.')
        end
        item
      end

      def self.register_pointcloud_points(name, points)
        return nil if points.empty?

        color = GaussianPoints.overlay&.visible_color || Sketchup::Color.new(80, 80, 80, 180)
        packed = points.flat_map { |pt| [pt.x.to_f, pt.y.to_f, pt.z.to_f, color.red.to_f, color.green.to_f, color.blue.to_f] }
        register_pointcloud_flat_data(name, packed)
      end

      def self.register_pointcloud_flat_data(name, flat_data, source_path: nil)
        return nil if flat_data.empty?

        prepared = prepare_centered_pointcloud(flat_data)
        project_path = source_path ? GaussianPoints::IO::GaspProject.cache_path_for_source(source_path) : nil
        if project_path
          written_project = GaussianPoints::IO::GaspProject.write_project(
            project_path: project_path,
            name: name,
            source_path: source_path,
            centered_points: prepared[:centered_data],
            center: prepared[:initial_state][:center],
            half_extents: prepared[:initial_state][:half_extents]
          )
          if written_project &&
             defined?(GaussianPoints::Hook) &&
             GaussianPoints::Hook.respond_to?(:supports_gasp_api?) &&
             GaussianPoints::Hook.supports_gasp_api?
            cached_item = GaussianPoints::UIparts::RenderItemRegistry.register_pointcloud_project(
              name: name,
              project_path: written_project,
              source_path: source_path
            )
            return cached_item if cached_item

            puts "[GASP] Native load failed for #{written_project}, falling back to direct point upload."
          end
        end

        GaussianPoints::UIparts::RenderItemRegistry.register_pointcloud(
          name: name,
          packed_points: prepared[:centered_data],
          initial_state: prepared[:initial_state]
        )
      end

      def self.try_import_fresh_gasp_cache(source_path)
        return false unless defined?(GaussianPoints::Hook) &&
                            GaussianPoints::Hook.respond_to?(:supports_gasp_api?) &&
                            GaussianPoints::Hook.supports_gasp_api?

        cache_path = GaussianPoints::IO::GaspProject.fresh_cache_for(source_path)
        return false unless cache_path

        !import_gasp(cache_path, display_name: File.basename(source_path), source_path: source_path).nil?
      end

      def self.pointcloud_bounds(flat_data)
        count = flat_data.length / 6
        min_x = max_x = flat_data[0].to_f
        min_y = max_y = flat_data[1].to_f
        min_z = max_z = flat_data[2].to_f

        count.times do |index|
          base = index * 6
          x = flat_data[base + 0].to_f
          y = flat_data[base + 1].to_f
          z = flat_data[base + 2].to_f
          min_x = [min_x, x].min
          min_y = [min_y, y].min
          min_z = [min_z, z].min
          max_x = [max_x, x].max
          max_y = [max_y, y].max
          max_z = [max_z, z].max
        end

        center = Geom::Point3d.new(
          (min_x + max_x) * 0.5,
          (min_y + max_y) * 0.5,
          (min_z + max_z) * 0.5
        )
        half_extents = {
          x: [((max_x - min_x) * 0.5), 1.mm].max,
          y: [((max_y - min_y) * 0.5), 1.mm].max,
          z: [((max_z - min_z) * 0.5), 1.mm].max
        }

        [center, half_extents]
      end

      def self.prepare_centered_pointcloud(flat_data)
        center, half_extents = pointcloud_bounds(flat_data)
        centered_data = Array.new(flat_data.length)
        count = flat_data.length / 6
        count.times do |index|
          base = index * 6
          centered_data[base + 0] = flat_data[base + 0].to_f - center.x
          centered_data[base + 1] = flat_data[base + 1].to_f - center.y
          centered_data[base + 2] = flat_data[base + 2].to_f - center.z
          centered_data[base + 3] = flat_data[base + 3].to_f
          centered_data[base + 4] = flat_data[base + 4].to_f
          centered_data[base + 5] = flat_data[base + 5].to_f
        end

        {
          centered_data: centered_data,
          initial_state: {
            center: center,
            half_extents: half_extents,
            axes: {
              x: Geom::Vector3d.new(1, 0, 0),
              y: Geom::Vector3d.new(0, 1, 0),
              z: Geom::Vector3d.new(0, 0, 1)
            }
          }
        }
      end

      def self.read_xyz_flat_data(filename)
        color = GaussianPoints.overlay&.visible_color || Sketchup::Color.new(80, 80, 80, 180)
        flat_data = []
        File.foreach(filename) do |line|
          next if line.strip.empty?

          cols = line.split
          next if cols.length < 3

          flat_data << cols[0].to_f
          flat_data << cols[1].to_f
          flat_data << cols[2].to_f
          if cols.length >= 6
            flat_data << cols[3].to_f
            flat_data << cols[4].to_f
            flat_data << cols[5].to_f
          else
            flat_data << color.red.to_f
            flat_data << color.green.to_f
            flat_data << color.blue.to_f
          end
        end
        flat_data
      end

    end
  end
end
