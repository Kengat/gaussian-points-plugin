module GaussianPoints
  module IO
    module Importer
      def self.import_dialog
        filters = 'XYZ|*.xyz|E57|*.e57||All|*.*||'
        filename = UI.openpanel('Select file', '', filters)
        return unless filename

        ext = File.extname(filename).downcase
        case ext
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

        lines = File.readlines(filename, chomp: true).reject(&:empty?)
        points = []
        lines.each do |line|
          cols = line.split.map(&:to_f)
          next if cols.size < 3

          x, y, z = cols
          points << Geom::Point3d.new(x, y, z)
        end

        if GaussianPoints::Hook.supports_object_api?
          item = register_pointcloud_points(File.basename(filename), points)
          if item
            UI.messagebox("Imported #{points.size} points from #{File.basename(filename)}")
          else
            UI.messagebox('Point cloud import failed.')
          end
        else
          overlay = GaussianPoints.overlay
          if overlay
            overlay.add_points(points)
            UI.messagebox("Imported #{points.size} points from #{File.basename(filename)}")
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
        colored_data.each do |(pt, _rr, _gg, _bb)|
          pt.x *= scale_factor
          pt.y *= scale_factor
          pt.z *= scale_factor
        end

        points_array = colored_data.flat_map { |pt, r, g, b| [pt.x, pt.y, pt.z, r.to_f, g.to_f, b.to_f] }

        optimized_array = GaussianPoints::IO::OctreeProcessor.process(points_array)
        optimized_count = optimized_array.size / 6
        puts "[Optimization] Reduced to #{optimized_count} points."
        GaussianPoints::SceneBoundsProxy.update_pointcloud_flat_array(optimized_array, stride: 6) if defined?(GaussianPoints::SceneBoundsProxy)

        if defined?(GaussianPoints::Hook) && GaussianPoints::Hook.supports_object_api?
          item = register_pointcloud_flat_data(File.basename(filename), optimized_array)
          if item
            UI.messagebox("Imported #{colored_data.size} points from #{File.basename(filename)}. Optimized to #{optimized_count} points.")
          else
            UI.messagebox('Point cloud import failed after optimization.')
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
            UI.messagebox("Imported #{colored_data.size} points from #{File.basename(filename)}. Optimized to #{optimized_count} points.")
          else
            UI.messagebox('Overlay not found.')
          end
        end
      end

      def self.register_pointcloud_points(name, points)
        return nil if points.empty?

        color = GaussianPoints.overlay&.visible_color || Sketchup::Color.new(80, 80, 80, 180)
        packed = points.flat_map { |pt| [pt.x.to_f, pt.y.to_f, pt.z.to_f, color.red.to_f, color.green.to_f, color.blue.to_f] }
        register_pointcloud_flat_data(name, packed)
      end

      def self.register_pointcloud_flat_data(name, flat_data)
        return nil if flat_data.empty?

        center, half_extents = pointcloud_bounds(flat_data)
        centered_data = []
        count = flat_data.length / 6
        count.times do |index|
          base = index * 6
          centered_data.concat([
            flat_data[base + 0] - center.x,
            flat_data[base + 1] - center.y,
            flat_data[base + 2] - center.z,
            flat_data[base + 3],
            flat_data[base + 4],
            flat_data[base + 5]
          ])
        end

        GaussianPoints::UIparts::RenderItemRegistry.register_pointcloud(
          name: name,
          packed_points: centered_data,
          initial_state: {
            center: center,
            half_extents: half_extents,
            axes: {
              x: Geom::Vector3d.new(1, 0, 0),
              y: Geom::Vector3d.new(0, 1, 0),
              z: Geom::Vector3d.new(0, 0, 1)
            }
          }
        )
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
    end
  end
end
