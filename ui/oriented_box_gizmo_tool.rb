module GaussianPoints
  module UIparts
    class OrientedBoxGizmoTool
      RESIZE_PICK_THRESHOLD = 16.0
      PLANE_PICK_THRESHOLD = 18.0
      CENTER_PICK_THRESHOLD = 18.0
      ROTATE_PICK_THRESHOLD = 22.0
      MOVE_PICK_THRESHOLD = 12.0

      def initialize(controller)
        @controller = controller
      end

      def activate(view = nil)
        @drag = nil
        @controller.set_active_handle(handle_none)
        view&.invalidate
      end

      def deactivate(view = nil)
        finish_drag(view, clear_hover: true, cancelled: true)
      end

      def onCancel(_reason, view = nil)
        finish_drag(view, clear_hover: true, cancelled: true)
      end

      def onMouseMove(flags, x, y, view)
        @last_mouse_x = x
        @last_mouse_y = y
        if @drag
          apply_drag(x, y, view)
        else
          @controller.set_hovered_handle(pick_handle(x, y, view, flags))
        end
      end

      def onLButtonDown(flags, x, y, view)
        handle_id = pick_handle(x, y, view, flags)
        return if handle_id == handle_none

        start_drag(handle_id, x, y, view, flags)
      end

      def onLButtonUp(_flags, _x, _y, view)
        finish_drag(view)
      end

      def onKeyDown(_key, _repeat, flags, view)
        refresh_hover_from_modifiers(flags, view)
      end

      def onKeyUp(_key, _repeat, flags, view)
        refresh_hover_from_modifiers(flags, view)
      end

      private

      def start_drag(handle_id, x, y, view, flags = 0)
        role = @controller.handle_role(handle_id)
        return unless role

        start_state = @controller.snapshot
        return unless start_state

        @drag =
          case role[:type]
          when :move
            axis = @controller.axis_vector(role[:axis], start_state)
            plane_origin = @controller.box_center(start_state)
            plane_normal = plane_normal_for_axis(axis, view.camera.direction)
            hit_point = intersect_mouse_with_plane(view, x, y, plane_origin, plane_normal) || plane_origin

            {
              handle_id: handle_id,
              role: role,
              start_state: start_state,
              plane_origin: plane_origin,
              plane_normal: plane_normal,
              axis: axis,
              start_scalar: scalar_on_axis(hit_point, plane_origin, axis)
            }
          when :resize
            axis = @controller.axis_vector(role[:axis], start_state)
            plane_origin = @controller.handle_point(handle_id, view, state: start_state)
            plane_normal = plane_normal_for_axis(axis, view.camera.direction)
            hit_point = intersect_mouse_with_plane(view, x, y, plane_origin, plane_normal) || plane_origin

            {
              handle_id: handle_id,
              role: role,
              start_state: start_state,
              plane_origin: plane_origin,
              plane_normal: plane_normal,
              axis: axis,
              start_scalar: scalar_on_axis(hit_point, plane_origin, axis)
            }
          when :move_plane
            axis_a = @controller.axis_vector(role[:axes][0], start_state)
            axis_b = @controller.axis_vector(role[:axes][1], start_state)
            plane_origin = @controller.box_center(start_state)
            plane_normal = @controller.axis_vector(role[:normal_axis], start_state)
            hit_point = intersect_mouse_with_plane(view, x, y, plane_origin, plane_normal) || plane_origin

            {
              handle_id: handle_id,
              role: role,
              start_state: start_state,
              plane_origin: plane_origin,
              plane_normal: plane_normal,
              axis_a: axis_a,
              axis_b: axis_b,
              start_hit_point: hit_point
            }
          when :rotate
            definition = @controller.rotation_handle_definition(handle_id, view, state: start_state)
            return unless definition

            plane_origin = definition[:center]
            plane_normal = @controller.axis_vector(role[:axis], start_state)
            hit_point = intersect_mouse_with_plane(view, x, y, plane_origin, plane_normal) || definition[:midpoint]

            {
              handle_id: handle_id,
              role: role,
              start_state: start_state,
              plane_origin: plane_origin,
              plane_normal: plane_normal,
              axis_a: definition[:axis_a],
              axis_b: definition[:axis_b],
              start_angle: angle_on_plane(hit_point, plane_origin, definition[:axis_a], definition[:axis_b])
            }
          when :move_center
            plane_origin = @controller.box_center(start_state)
            plane_normal = view.camera.direction
            hit_point = intersect_mouse_with_plane(view, x, y, plane_origin, plane_normal) || plane_origin

            {
              handle_id: handle_id,
              role: role,
              start_state: start_state,
              plane_origin: plane_origin,
              plane_normal: plane_normal,
              start_hit_point: hit_point
            }
          when :scale_uniform
            {
              handle_id: handle_id,
              role: role,
              start_state: start_state,
              center_point: @controller.box_center(start_state),
              start_screen: [x.to_f, y.to_f],
              flags: flags
            }
          end

        if @drag
          @drag[:operation_started] =
            @controller.respond_to?(:begin_interaction_operation) &&
            @controller.begin_interaction_operation(@drag)
          @drag[:changed] = false
          @controller.set_active_handle(handle_id)
        end
      end

      def apply_drag(x, y, view)
        return unless @drag

        role = @drag[:role]

        next_state =
          case role[:type]
          when :move
            hit_point = intersect_mouse_with_plane(view, x, y, @drag[:plane_origin], @drag[:plane_normal])
            return unless hit_point
            scalar = scalar_on_axis(hit_point, @drag[:plane_origin], @drag[:axis])
            delta = scalar - @drag[:start_scalar]
            @controller.translated_snapshot(@drag[:start_state], scaled_vector(@drag[:axis], delta))
          when :resize
            hit_point = intersect_mouse_with_plane(view, x, y, @drag[:plane_origin], @drag[:plane_normal])
            return unless hit_point
            scalar = scalar_on_axis(hit_point, @drag[:plane_origin], @drag[:axis])
            delta = scalar - @drag[:start_scalar]
            @controller.resized_snapshot(@drag[:start_state], role[:axis], role[:side], delta)
          when :move_plane
            hit_point = intersect_mouse_with_plane(view, x, y, @drag[:plane_origin], @drag[:plane_normal])
            return unless hit_point
            delta_vector = hit_point - @drag[:start_hit_point]
            translation = scaled_vector(@drag[:axis_a], delta_vector.dot(@drag[:axis_a]))
            translation = translation + scaled_vector(@drag[:axis_b], delta_vector.dot(@drag[:axis_b]))
            @controller.translated_snapshot(@drag[:start_state], translation)
          when :rotate
            hit_point = intersect_mouse_with_plane(view, x, y, @drag[:plane_origin], @drag[:plane_normal])
            return unless hit_point
            current_angle = angle_on_plane(hit_point, @drag[:plane_origin], @drag[:axis_a], @drag[:axis_b])
            delta_angle = normalize_angle(current_angle - @drag[:start_angle])
            @controller.rotated_snapshot(@drag[:start_state], role[:axis], delta_angle)
          when :move_center
            hit_point = intersect_mouse_with_plane(view, x, y, @drag[:plane_origin], @drag[:plane_normal])
            return unless hit_point
            @controller.translated_snapshot(@drag[:start_state], hit_point - @drag[:start_hit_point])
          when :scale_uniform
            delta_pixels = signed_uniform_scale_pixels(x, y)
            delta_model = view.pixels_to_model(delta_pixels.abs, @drag[:center_point]).to_f
            delta_model *= -1.0 if delta_pixels < 0.0
            @controller.uniformly_scaled_snapshot(@drag[:start_state], delta_model)
          end

        if next_state
          changed =
            if @controller.respond_to?(:snapshots_equal?)
              !@controller.snapshots_equal?(@drag[:start_state], next_state)
            else
              true
            end
          @drag[:changed] ||= changed
          @controller.apply_snapshot(next_state)
        end
      end

      def finish_drag(view, clear_hover: false, cancelled: false)
        drag = @drag
        if drag
          if cancelled
            @controller.apply_snapshot(drag[:start_state]) if drag[:changed]
            @controller.abort_interaction_operation(drag) if drag[:operation_started] && @controller.respond_to?(:abort_interaction_operation)
          elsif drag[:changed]
            @controller.commit_interaction_operation(drag) if drag[:operation_started] && @controller.respond_to?(:commit_interaction_operation)
          else
            @controller.abort_interaction_operation(drag) if drag[:operation_started] && @controller.respond_to?(:abort_interaction_operation)
          end
        end

        @drag = nil
        @controller.set_active_handle(handle_none)
        @controller.set_hovered_handle(handle_none) if clear_hover
        view.invalidate if view
      end

      def pick_handle(x, y, view, flags = 0)
        resize_handle = nearest_resize_handle(x, y, view)
        return resize_handle if resize_handle

        plane_handle = nearest_plane_handle(x, y, view)
        return plane_handle if plane_handle

        center_handle = center_handle_at(x, y, view, flags)
        return center_handle if center_handle

        rotate_handle = nearest_rotate_handle(x, y, view)
        return rotate_handle if rotate_handle

        nearest_move_handle(x, y, view) || handle_none
      end

      def nearest_resize_handle(x, y, view)
        nearest_point_handle(x, y, view, @controller::RESIZE_HANDLE_IDS, RESIZE_PICK_THRESHOLD) do |handle_id|
          @controller.handle_point(handle_id, view)
        end
      end

      def nearest_plane_handle(x, y, view)
        nearest_point_handle(x, y, view, @controller::PLANE_HANDLE_IDS, PLANE_PICK_THRESHOLD) do |handle_id|
          @controller.handle_point(handle_id, view)
        end
      end

      def nearest_rotate_handle(x, y, view)
        best_handle = nil
        best_distance = ROTATE_PICK_THRESHOLD

        @controller::ROTATE_HANDLE_IDS.each do |handle_id|
          definition = @controller.rotation_handle_definition(handle_id, view)
          next unless definition

          screen_points = definition[:points].map { |point| view.screen_coords(point) }
          distance = distance_to_polyline_2d(x, y, screen_points)
          next unless distance < best_distance

          best_handle = handle_id
          best_distance = distance
        end

        best_handle
      end

      def center_handle_at(x, y, view, flags)
        center_handle_id = control_down?(flags) ? @controller::HANDLE_SCALE_UNIFORM : @controller::HANDLE_MOVE_CENTER
        nearest_point_handle(x, y, view, [center_handle_id], CENTER_PICK_THRESHOLD) do |handle_id|
          @controller.handle_point(handle_id, view)
        end
      end

      def nearest_move_handle(x, y, view)
        best_handle = nil
        best_distance = MOVE_PICK_THRESHOLD

        @controller::MOVE_HANDLE_IDS.each do |handle_id|
          segment = @controller.handle_segment(handle_id, view)
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
          point_3d = yield(handle_id)
          next unless point_3d

          point_2d = view.screen_coords(point_3d)
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

      def angle_on_plane(point, origin, axis_a, axis_b)
        vector = point - origin
        Math.atan2(vector.dot(axis_b), vector.dot(axis_a))
      end

      def normalize_angle(angle)
        while angle > Math::PI
          angle -= Math::PI * 2.0
        end
        while angle < -Math::PI
          angle += Math::PI * 2.0
        end
        angle
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

      def scaled_vector(vector, length)
        scaled = vector.clone
        scaled.normalize!
        scaled.length = length.abs
        scaled.reverse! if length < 0.0
        scaled
      end

      def distance_to_polyline_2d(px, py, points)
        return Float::INFINITY if points.length < 2

        points.each_cons(2).reduce(Float::INFINITY) do |best, (start_2d, end_2d)|
          [best, distance_to_segment_2d(px, py, start_2d, end_2d)].min
        end
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

      def refresh_hover_from_modifiers(flags, view)
        return unless @last_mouse_x && @last_mouse_y
        return if @drag

        @controller.set_hovered_handle(pick_handle(@last_mouse_x, @last_mouse_y, view, flags))
      end

      def signed_uniform_scale_pixels(x, y)
        start_x, start_y = @drag[:start_screen]
        delta_x = x - start_x
        delta_y = y - start_y
        diagonal = Math.sqrt(0.5)
        (delta_x * diagonal) + (-delta_y * diagonal)
      end

      def control_down?(flags)
        mask = defined?(COPY_MODIFIER_MASK) ? COPY_MODIFIER_MASK : 4
        (flags.to_i & mask) != 0
      end

      def handle_none
        @controller::HANDLE_NONE
      end
    end
  end
end
