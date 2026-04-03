module GaussianPoints
  module UIparts
    class ClippingBoxTool
      RESIZE_PICK_THRESHOLD = 16.0
      PLANE_PICK_THRESHOLD = 18.0
      MOVE_PICK_THRESHOLD = 12.0

      def activate(view)
        @drag = nil
        ClippingManager.set_active_handle(ClippingManager::HANDLE_NONE)
        view.invalidate
      end

      def deactivate(view)
        finish_drag(view, clear_hover: true)
      end

      def onCancel(_reason, view)
        finish_drag(view, clear_hover: true)
      end

      def onMouseMove(_flags, x, y, view)
        if @drag
          apply_drag(x, y, view)
        else
          ClippingManager.set_hovered_handle(pick_handle(x, y, view))
        end
      end

      def onLButtonDown(_flags, x, y, view)
        handle_id = pick_handle(x, y, view)
        return if handle_id == ClippingManager::HANDLE_NONE

        start_drag(handle_id, x, y, view)
      end

      def onLButtonUp(_flags, _x, _y, view)
        finish_drag(view)
      end

      def draw(view)
        # Native overlay is the primary visualization path.
      end

      private

      def start_drag(handle_id, x, y, view)
        role = ClippingManager.handle_role(handle_id)
        return unless role

        @drag =
          case role[:type]
          when :move
            axis = ClippingManager.axis_vector(role[:axis])
            plane_origin = ClippingManager.box_center
            plane_normal = plane_normal_for_axis(axis, view.camera.direction)
            hit_point = intersect_mouse_with_plane(view, x, y, plane_origin, plane_normal) || plane_origin

            {
              handle_id: handle_id,
              role: role,
              plane_origin: plane_origin,
              plane_normal: plane_normal,
              start_scalar: scalar_on_axis(hit_point, plane_origin, axis),
              start_min: ClippingManager.box_min,
              start_max: ClippingManager.box_max
            }
          when :resize
            axis = ClippingManager.axis_vector(role[:axis])
            plane_origin = ClippingManager.handle_point(handle_id, view)
            plane_normal = plane_normal_for_axis(axis, view.camera.direction)
            hit_point = intersect_mouse_with_plane(view, x, y, plane_origin, plane_normal) || plane_origin

            {
              handle_id: handle_id,
              role: role,
              plane_origin: plane_origin,
              plane_normal: plane_normal,
              start_scalar: scalar_on_axis(hit_point, plane_origin, axis),
              start_min: ClippingManager.box_min,
              start_max: ClippingManager.box_max
            }
          when :move_plane
            plane_origin = ClippingManager.box_center
            plane_normal = ClippingManager.axis_vector(role[:normal_axis])
            hit_point = intersect_mouse_with_plane(view, x, y, plane_origin, plane_normal) || plane_origin

            {
              handle_id: handle_id,
              role: role,
              plane_origin: plane_origin,
              plane_normal: plane_normal,
              start_hit_point: hit_point,
              start_min: ClippingManager.box_min,
              start_max: ClippingManager.box_max
            }
          end

        ClippingManager.set_active_handle(handle_id)
      end

      def apply_drag(x, y, view)
        role = @drag[:role]
        hit_point = intersect_mouse_with_plane(view, x, y, @drag[:plane_origin], @drag[:plane_normal])
        return unless hit_point

        min_point = @drag[:start_min].clone
        max_point = @drag[:start_max].clone

        case role[:type]
        when :move
          axis = ClippingManager.axis_vector(role[:axis])
          scalar = scalar_on_axis(hit_point, @drag[:plane_origin], axis)
          delta = scalar - @drag[:start_scalar]
          apply_translation(min_point, max_point, role[:axis], delta)
        when :resize
          axis = ClippingManager.axis_vector(role[:axis])
          scalar = scalar_on_axis(hit_point, @drag[:plane_origin], axis)
          delta = scalar - @drag[:start_scalar]
          apply_resize(min_point, max_point, role[:axis], role[:side], delta)
        when :move_plane
          delta_vector = hit_point - @drag[:start_hit_point]
          apply_plane_translation(min_point, max_point, role[:axes], delta_vector)
        end

        ClippingManager.set_box_extents(min_point, max_point)
      end

      def finish_drag(view, clear_hover: false)
        @drag = nil
        ClippingManager.set_active_handle(ClippingManager::HANDLE_NONE)
        ClippingManager.set_hovered_handle(ClippingManager::HANDLE_NONE) if clear_hover
        view.invalidate if view
      end

      def apply_translation(min_point, max_point, axis, delta)
        case axis
        when :x
          min_point.x += delta
          max_point.x += delta
        when :y
          min_point.y += delta
          max_point.y += delta
        when :z
          min_point.z += delta
          max_point.z += delta
        end
      end

      def apply_resize(min_point, max_point, axis, side, delta)
        case [axis, side]
        when [:x, :min] then min_point.x += delta
        when [:x, :max] then max_point.x += delta
        when [:y, :min] then min_point.y += delta
        when [:y, :max] then max_point.y += delta
        when [:z, :min] then min_point.z += delta
        when [:z, :max] then max_point.z += delta
        end
      end

      def apply_plane_translation(min_point, max_point, axes, delta_vector)
        axes.each do |axis|
          delta = delta_vector.dot(ClippingManager.axis_vector(axis))
          apply_translation(min_point, max_point, axis, delta)
        end
      end

      def pick_handle(x, y, view)
        resize_handle = nearest_resize_handle(x, y, view)
        return resize_handle if resize_handle

        plane_handle = nearest_plane_handle(x, y, view)
        return plane_handle if plane_handle

        nearest_move_handle(x, y, view) || ClippingManager::HANDLE_NONE
      end

      def nearest_resize_handle(x, y, view)
        nearest_point_handle(x, y, view, ClippingManager::RESIZE_HANDLE_IDS, RESIZE_PICK_THRESHOLD) do |handle_id|
          ClippingManager.handle_point(handle_id, view)
        end
      end

      def nearest_plane_handle(x, y, view)
        nearest_point_handle(x, y, view, ClippingManager::PLANE_HANDLE_IDS, PLANE_PICK_THRESHOLD) do |handle_id|
          ClippingManager.handle_point(handle_id, view)
        end
      end

      def nearest_move_handle(x, y, view)
        best_handle = nil
        best_distance = MOVE_PICK_THRESHOLD

        ClippingManager::MOVE_HANDLE_IDS.each do |handle_id|
          segment = ClippingManager.handle_segment(handle_id, view)
          next unless segment

          start_2d = view.screen_coords(segment.first)
          end_2d = view.screen_coords(segment.last)
          distance = distance_to_segment_2d(x, y, start_2d, end_2d)
          next unless distance < best_distance

          best_handle = handle_id
          best_distance = distance
        end

        best_handle
      end

      def nearest_point_handle(x, y, view, handle_ids, threshold)
        best_handle = nil
        best_distance = threshold

        handle_ids.each do |handle_id|
          point_2d = view.screen_coords(yield(handle_id))
          distance = Math.hypot(x - point_2d.x, y - point_2d.y)
          next unless distance < best_distance

          best_handle = handle_id
          best_distance = distance
        end

        best_handle
      end

      def intersect_mouse_with_plane(view, x, y, origin, normal)
        ray = view.pickray(x, y)
        return nil unless ray

        Geom.intersect_line_plane(ray, [origin, normal])
      end

      def scalar_on_axis(point, origin, axis)
        vector = point - origin
        vector.dot(axis)
      end

      def plane_normal_for_axis(axis, camera_direction)
        tangent = camera_direction.cross(axis)
        normal = axis.cross(tangent)
        return normal if normal.length > 0.001

        fallback = Geom::Vector3d.new(0, 0, 1).cross(axis)
        normal = axis.cross(fallback)
        return normal if normal.length > 0.001

        Geom::Vector3d.new(0, 1, 0)
      end

      def distance_to_segment_2d(px, py, start_2d, end_2d)
        vx = end_2d.x - start_2d.x
        vy = end_2d.y - start_2d.y
        wx = px - start_2d.x
        wy = py - start_2d.y
        vv = (vx * vx) + (vy * vy)
        return Math.hypot(wx, wy) if vv <= 0.0001

        t = ((wx * vx) + (wy * vy)) / vv.to_f
        t = [[t, 0.0].max, 1.0].min
        closest_x = start_2d.x + (vx * t)
        closest_y = start_2d.y + (vy * t)
        Math.hypot(px - closest_x, py - closest_y)
      end
    end
  end
end
