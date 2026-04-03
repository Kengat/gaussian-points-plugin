module GaussianPoints
  module SceneBoundsProxy
    DICTIONARY_NAME = 'GaussianPoints'.freeze
    PROXY_KIND = 'scene_bounds_proxy'.freeze
    PROXY_NAME = '[GaussianPoints Bounds Proxy]'.freeze
    PROXY_MATERIAL = 'GaussianPoints Bounds Proxy Material'.freeze
    CLIP_BOX_SOURCE = :clip_box
    EPSILON = 1.mm

    @source_bounds = {}

    class << self
      def current_bounds_snapshot
        combined_bounds(include_clip_box: false)
      end

      def update_pointcloud_points(points)
        update_source_bounds(:pointcloud, bounds_from_points(points))
      end

      def update_pointcloud_flat_array(data, stride: 6)
        update_source_bounds(:pointcloud, bounds_from_flat_array(data, stride))
      end

      def update_splats_bounds(min_point, max_point = nil)
        snapshot =
          if max_point.nil?
            bounds_to_snapshot(min_point)
          else
            bounds_to_snapshot(bounds_from_min_max(min_point, max_point))
          end
        update_source_bounds(:splats, snapshot)
      end

      def clear_pointcloud
        clear_source_bounds(:pointcloud)
      end

      def clear_splats
        clear_source_bounds(:splats)
      end

      def update_clip_box_bounds(min_point, max_point)
        update_source_bounds(CLIP_BOX_SOURCE, bounds_to_snapshot(bounds_from_min_max(min_point, max_point)))
      end

      def clear_clip_box
        clear_source_bounds(CLIP_BOX_SOURCE)
      end

      private

      def update_source_bounds(source, snapshot)
        existing = @source_bounds[source]
        if snapshot.nil?
          return unless @source_bounds.delete(source)
        else
          return if existing == snapshot
          @source_bounds[source] = snapshot
        end
        refresh_proxy
      end

      def clear_source_bounds(source)
        return unless @source_bounds.delete(source)

        refresh_proxy
      end

      def refresh_proxy
        model = Sketchup.active_model
        return unless model

        combined = combined_bounds(include_clip_box: true)

        model.start_operation('Gaussian Points Bounds', true, false, true)
        proxy_group = find_proxy_group(model)

        if combined.nil?
          erase_proxy_group(proxy_group)
        else
          erase_proxy_group(proxy_group)
          build_proxy_group(model, combined)
        end

        model.commit_operation
        model.active_view.invalidate
      rescue StandardError => e
        model.abort_operation if model
        puts "[GaussianPoints::SceneBoundsProxy] #{e.class}: #{e.message}"
      end

      def combined_bounds(include_clip_box: true)
        snapshots = @source_bounds.each_with_object([]) do |(source, snapshot), acc|
          next if snapshot.nil?
          next if !include_clip_box && source == CLIP_BOX_SOURCE

          acc << snapshot
        end
        return nil if snapshots.empty?

        bb = Geom::BoundingBox.new
        snapshots.each do |snapshot|
          bb.add(Geom::Point3d.new(*snapshot[:min]))
          bb.add(Geom::Point3d.new(*snapshot[:max]))
        end
        bounds_to_snapshot(bb)
      end

      def build_proxy_group(model, snapshot)
        min = Geom::Point3d.new(*snapshot[:min])
        max = Geom::Point3d.new(*snapshot[:max])
        max = Geom::Point3d.new(min.x + EPSILON, min.y + EPSILON, min.z + EPSILON) if same_point?(min, max)

        group = model.entities.add_group
        group.name = PROXY_NAME
        group.set_attribute(DICTIONARY_NAME, 'kind', PROXY_KIND)
        material = proxy_material(model)
        add_marker_face(group.entities, min, +EPSILON, +EPSILON, material)
        add_marker_face(group.entities, max, -EPSILON, -EPSILON, material)
        group.locked = true
      end

      def erase_proxy_group(group)
        return unless group && !group.deleted?

        group.locked = false if group.respond_to?(:locked=)
        group.erase!
      end

      def find_proxy_group(model)
        model.entities.grep(Sketchup::Group).find do |group|
          !group.deleted? && group.get_attribute(DICTIONARY_NAME, 'kind') == PROXY_KIND
        end
      end

      def proxy_material(model)
        materials = model.materials
        material = materials[PROXY_MATERIAL] || materials.add(PROXY_MATERIAL)
        material.color = Sketchup::Color.new(255, 255, 255)
        material.alpha = 0.0
        material
      end

      def add_marker_face(entities, origin, x_offset, y_offset, material)
        x_point = Geom::Point3d.new(origin.x + x_offset, origin.y, origin.z)
        y_point = Geom::Point3d.new(origin.x, origin.y + y_offset, origin.z)
        face = entities.add_face(origin, x_point, y_point)
        return unless face

        face.material = material
        face.back_material = material
        face.edges.each do |edge|
          edge.hidden = true
          edge.soft = true if edge.respond_to?(:soft=)
          edge.smooth = true if edge.respond_to?(:smooth=)
        end
      end

      def bounds_from_points(points)
        return nil if points.nil? || points.empty?

        bb = Geom::BoundingBox.new
        points.each do |point|
          bb.add(point)
        end
        bounds_to_snapshot(bb)
      end

      def bounds_from_flat_array(data, stride)
        return nil if data.nil? || data.empty?

        bb = Geom::BoundingBox.new
        count = data.length / stride
        count.times do |index|
          base = index * stride
          bb.add(Geom::Point3d.new(data[base], data[base + 1], data[base + 2]))
        end
        bounds_to_snapshot(bb)
      end

      def bounds_from_min_max(min_point, max_point)
        bb = Geom::BoundingBox.new
        bb.add(Geom::Point3d.new(*min_point))
        bb.add(Geom::Point3d.new(*max_point))
        bb
      end

      def bounds_to_snapshot(bounds)
        return nil if bounds.nil?

        bb =
          if bounds.is_a?(Geom::BoundingBox)
            bounds
          else
            box = Geom::BoundingBox.new
            box.add(bounds)
            box
          end
        return nil if bb.empty?

        min = bb.min
        max = bb.max
        {
          min: [min.x.to_f, min.y.to_f, min.z.to_f],
          max: [max.x.to_f, max.y.to_f, max.z.to_f]
        }
      end

      def same_point?(point_a, point_b)
        point_a.distance(point_b) <= EPSILON
      end
    end
  end
end
