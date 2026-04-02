module GaussianPoints
  module IO
    module Exporter

      def self.export_xyz
        model = Sketchup.active_model
        entities = model.active_entities # Use active_entities for direct selection

        # Collect vertices directly, handling different entity types
        vertices = []
        entities.each do |entity|
          case entity
          when Sketchup::Edge
            vertices << entity.start
            vertices << entity.end
          when Sketchup::Face
            entity.vertices.each { |v| vertices << v }
          when Sketchup::ComponentInstance, Sketchup::Group
            # Recursively process entities within components/groups
            entity.definition.entities.each { |e|
              case e  # Nested case for handling different types inside
              when Sketchup::Edge
                vertices << e.start
                vertices << e.end
              when Sketchup::Face
                e.vertices.each { |v| vertices << v }
              end
            }
          end
        end

        if vertices.empty?
          UI.messagebox("Нет вершин для экспорта.")
          return
        end

        filename = UI.savepanel("Сохранить .xyz", "", "model.xyz")
        return unless filename
        filename += ".xyz" unless File.extname(filename).downcase == ".xyz"

        # Use a Set for efficient uniqueness and fast lookup.
        unique_vertices = Set.new

        # Ensure uniqueness based on position, using a tolerance.  This
        # is CRUCIAL for SketchUp because vertices that *look* like they
        # are in the same place might have very slightly different
        # coordinates due to floating-point inaccuracies.
        tolerance = 1e-6 # Adjust as needed for your model's precision.
        vertices.each do |vertex|
          existing_vertex = unique_vertices.find { |v| v.position.distance(vertex.position) < tolerance }
          if existing_vertex.nil?
            unique_vertices.add(vertex)
          end
        end


        File.open(filename, "w") do |f|
          unique_vertices.each do |vertex|
            pos = vertex.position
            f.puts format("%.6f %.6f %.6f", pos.x.to_f, pos.y.to_f, pos.z.to_f) # Explicitly use to_f
          end
        end

        UI.messagebox("Экспортировано #{unique_vertices.size} уникальных точек в #{File.basename(filename)}")
      end

    end
  end
end
