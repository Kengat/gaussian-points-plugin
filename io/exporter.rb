require 'set'

module GaussianPoints
  module IO
    module Exporter
      def self.export_xyz
        model = Sketchup.active_model
        entities = model.active_entities
        vertices = []

        entities.each do |entity|
          case entity
          when Sketchup::Edge
            vertices << entity.start
            vertices << entity.end
          when Sketchup::Face
            entity.vertices.each { |v| vertices << v }
          when Sketchup::ComponentInstance, Sketchup::Group
            entity.definition.entities.each do |nested|
              case nested
              when Sketchup::Edge
                vertices << nested.start
                vertices << nested.end
              when Sketchup::Face
                nested.vertices.each { |v| vertices << v }
              end
            end
          end
        end

        if vertices.empty?
          UI.messagebox('No vertices found for export.')
          return
        end

        filename = UI.savepanel('Save .xyz', '', 'model.xyz')
        return unless filename

        filename += '.xyz' unless File.extname(filename).downcase == '.xyz'
        unique_vertices = Set.new
        tolerance = 1e-6

        vertices.each do |vertex|
          existing_vertex = unique_vertices.find { |v| v.position.distance(vertex.position) < tolerance }
          unique_vertices.add(vertex) if existing_vertex.nil?
        end

        File.open(filename, 'w') do |file|
          unique_vertices.each do |vertex|
            pos = vertex.position
            file.puts format('%.6f %.6f %.6f', pos.x.to_f, pos.y.to_f, pos.z.to_f)
          end
        end

        UI.messagebox("Exported #{unique_vertices.size} unique points to #{File.basename(filename)}")
      end
    end
  end
end
