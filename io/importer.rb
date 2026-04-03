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

        overlay = GaussianPoints.overlay
        if overlay
          overlay.add_points(points)
          UI.messagebox("Imported #{points.size} points from #{File.basename(filename)}")
        else
          UI.messagebox('Overlay not found.')
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

        points_array = colored_data.map { |pt, r, g, b|
          [pt.x, pt.y, pt.z, r.to_f, g.to_f, b.to_f]
        }.flatten

        optimized_array = GaussianPoints::IO::OctreeProcessor.process(points_array)
        optimized_count = optimized_array.size / 6
        puts "[Optimization] Reduced to #{optimized_count} points."

        if defined?(GaussianPoints::Hook) && GaussianPoints::Hook.set_pointcloud(optimized_array)
          UI.messagebox("Imported #{colored_data.size} points from #{File.basename(filename)}. Optimized to #{optimized_count} points.")
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
    end
  end
end
