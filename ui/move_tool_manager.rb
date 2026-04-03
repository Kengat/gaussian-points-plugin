module GaussianPoints
  module UIparts
    module MoveToolManager
      extend self

      HANDLE_NONE = 0
      HANDLE_MOVE_X = 1
      HANDLE_MOVE_Y = 2
      HANDLE_MOVE_Z = 3
      HANDLE_RESIZE_MIN_X = 4
      HANDLE_RESIZE_MAX_X = 5
      HANDLE_RESIZE_MIN_Y = 6
      HANDLE_RESIZE_MAX_Y = 7
      HANDLE_RESIZE_MIN_Z = 8
      HANDLE_RESIZE_MAX_Z = 9
      HANDLE_MOVE_PLANE_XY = 10
      HANDLE_MOVE_PLANE_XZ = 11
      HANDLE_MOVE_PLANE_YZ = 12
      HANDLE_ROTATE_X = 13
      HANDLE_ROTATE_Y = 14
      HANDLE_ROTATE_Z = 15
      HANDLE_MOVE_CENTER = 16
      HANDLE_SCALE_UNIFORM = 17

      MOVE_HANDLE_IDS = [HANDLE_MOVE_X, HANDLE_MOVE_Y, HANDLE_MOVE_Z].freeze
      RESIZE_HANDLE_IDS = [HANDLE_RESIZE_MIN_X, HANDLE_RESIZE_MAX_X, HANDLE_RESIZE_MIN_Y, HANDLE_RESIZE_MAX_Y, HANDLE_RESIZE_MIN_Z, HANDLE_RESIZE_MAX_Z].freeze
      PLANE_HANDLE_IDS = [HANDLE_MOVE_PLANE_XY, HANDLE_MOVE_PLANE_XZ, HANDLE_MOVE_PLANE_YZ].freeze
      ROTATE_HANDLE_IDS = [HANDLE_ROTATE_X, HANDLE_ROTATE_Y, HANDLE_ROTATE_Z].freeze
      CENTER_HANDLE_IDS = [HANDLE_MOVE_CENTER, HANDLE_SCALE_UNIFORM].freeze
      HANDLE_IDS = (MOVE_HANDLE_IDS + RESIZE_HANDLE_IDS + PLANE_HANDLE_IDS + ROTATE_HANDLE_IDS + CENTER_HANDLE_IDS).freeze

      MIN_BOX_SIZE = 10.mm
      MOVE_GAP_PIXELS = 18.0
      MOVE_LENGTH_PIXELS = 54.0
      PLANE_OFFSET_PIXELS = 24.0
      PLANE_SIZE_PIXELS = 18.0
      ROTATE_RADIUS_PIXELS = 120.0
      ROTATE_SEGMENTS = 24
      ROTATE_SWEEP = Math::PI * 0.32

      def activate
        @tool_active = true
        refresh_native_state!
      end

      def deactivate
        @tool_active = false
        set_hovered_handle(HANDLE_NONE)
        set_active_handle(HANDLE_NONE)
        set_hovered_item(nil)
        refresh_native_state!
      end

      def tool_active?
        @tool_active == true
      end

      def selected_item
        RenderItemRegistry.find(@selected_item_id)
      end

      def selected?
        !selected_item.nil?
      end

      def hovered_item
        RenderItemRegistry.find(@hovered_item_id)
      end

      def hovered_handle
        @hovered_handle || HANDLE_NONE
      end

      def active_handle
        @active_handle || HANDLE_NONE
      end

      def set_hovered_item(item_id)
        return if @hovered_item_id == item_id

        @hovered_item_id = item_id
        sync_item_highlights!
        invalidate_view
      end

      def select_item(item_id)
        return clear_selection unless item_id

        @selected_item_id = item_id
        @hovered_item_id = nil
        set_hovered_handle(HANDLE_NONE)
        set_active_handle(HANDLE_NONE)
        sync_item_highlights!
        invalidate_view
      end

      def clear_selection
        changed = !@selected_item_id.nil? || hovered_handle != HANDLE_NONE || active_handle != HANDLE_NONE
        @selected_item_id = nil
        @hovered_item_id = nil
        @hovered_handle = HANDLE_NONE
        @active_handle = HANDLE_NONE
        sync_item_highlights!
        invalidate_view if changed
      end

      def set_hovered_handle(handle_id)
        normalized = normalize_handle(handle_id)
        return if hovered_handle == normalized

        @hovered_handle = normalized
        invalidate_view
      end

      def set_active_handle(handle_id)
        normalized = normalize_handle(handle_id)
        return if active_handle == normalized

        @active_handle = normalized
        invalidate_view
      end

      def pick_item(x, y, view)
        ray = view.pickray(x, y)
        return nil unless ray

        best_item = nil
        best_distance = Float::INFINITY

        RenderItemRegistry.ordered_items.reverse_each do |item|
          distance = ray_intersection_distance(item[:box_state], ray)
          next unless distance
          next unless distance < best_distance

          best_item = item
          best_distance = distance
        end

        best_item&.dig(:id)
      end

      def snapshot
        item = selected_item
        item ? RenderItemRegistry.duplicate_snapshot(item[:box_state]) : nil
      end

      def apply_snapshot(state)
        item = selected_item
        return unless item && state

        RenderItemRegistry.update_item_state(item[:id], normalize_snapshot(state), persist: false)
        invalidate_view
      end

      def snapshots_equal?(left, right, epsilon = 1.0e-6)
        return false unless left && right

        left_center = left[:center]
        right_center = right[:center]
        return false unless left_center.distance(right_center) <= epsilon

        %i[x y z].all? do |axis|
          (left[:half_extents][axis].to_f - right[:half_extents][axis].to_f).abs <= epsilon &&
            (left[:axes][axis].x.to_f - right[:axes][axis].x.to_f).abs <= epsilon &&
            (left[:axes][axis].y.to_f - right[:axes][axis].y.to_f).abs <= epsilon &&
            (left[:axes][axis].z.to_f - right[:axes][axis].z.to_f).abs <= epsilon
        end
      end

      def begin_interaction_operation(context = nil)
        model = Sketchup.active_model
        return false unless model

        model.start_operation(interaction_operation_name(context), true)
        true
      end

      def commit_interaction_operation(_context = nil)
        RenderItemRegistry.persist_to_model!
        Sketchup.active_model.commit_operation
      rescue StandardError
        nil
      end

      def abort_interaction_operation(_context = nil)
        Sketchup.active_model.abort_operation
      rescue StandardError
        nil
      end

      def reset_selected_item_transform
        item = selected_item
        return false unless item

        target_state = RenderItemRegistry.base_snapshot(item[:id])
        return false unless target_state
        return false if snapshots_equal?(item[:box_state], target_state)

        model = Sketchup.active_model
        return false unless model

        model.start_operation('Reset Move Transform', true)
        RenderItemRegistry.update_item_state(item[:id], target_state, persist: true)
        invalidate_view
        model.commit_operation
        true
      rescue StandardError
        model&.abort_operation
        false
      end

      def box_center(state = nil)
        current = state || snapshot
        current ? current[:center].clone : Geom::Point3d.new(0, 0, 0)
      end

      def axis_vector(axis, state = nil)
        current = state || snapshot
        vector = current && current[:axes][axis]
        normalize_vector(vector, axis)
      end

      def half_extent(axis, state = nil)
        current = state || snapshot
        value = current && current[:half_extents][axis]
        [value.to_f, MIN_BOX_SIZE * 0.5].max
      end

      def translated_snapshot(state, vector)
        {
          center: state[:center].offset(vector),
          half_extents: state[:half_extents].dup,
          axes: state[:axes].transform_values(&:clone)
        }
      end

      def resized_snapshot(state, axis, side, delta)
        sign = side == :max ? 1.0 : -1.0
        current_half = state[:half_extents][axis].to_f
        target_half = current_half + (delta * sign * 0.5)
        new_half = [target_half, MIN_BOX_SIZE * 0.5].max
        applied_delta = (new_half - current_half) * 2.0 * sign

        {
          center: state[:center].offset(scaled_vector(axis_vector(axis, state), applied_delta * 0.5)),
          half_extents: state[:half_extents].merge(axis => new_half),
          axes: state[:axes].transform_values(&:clone)
        }
      end

      def uniformly_scaled_snapshot(state, delta)
        factor = uniform_scale_factor(state, delta)
        {
          center: state[:center].clone,
          half_extents: state[:half_extents].transform_values { |value| [value.to_f * factor, MIN_BOX_SIZE * 0.5].max },
          axes: state[:axes].transform_values(&:clone)
        }
      end

      def rotated_snapshot(state, axis, angle)
        rotation = Geom::Transformation.rotation(state[:center], axis_vector(axis, state), angle)
        {
          center: state[:center].clone,
          half_extents: state[:half_extents].dup,
          axes: {
            x: normalize_vector(state[:axes][:x].transform(rotation), :x),
            y: normalize_vector(state[:axes][:y].transform(rotation), :y),
            z: normalize_vector(state[:axes][:z].transform(rotation), :z)
          }
        }
      end

      def handle_role(handle_id)
        case handle_id
        when HANDLE_MOVE_X then { type: :move, axis: :x }
        when HANDLE_MOVE_Y then { type: :move, axis: :y }
        when HANDLE_MOVE_Z then { type: :move, axis: :z }
        when HANDLE_RESIZE_MIN_X then { type: :resize, axis: :x, side: :min }
        when HANDLE_RESIZE_MAX_X then { type: :resize, axis: :x, side: :max }
        when HANDLE_RESIZE_MIN_Y then { type: :resize, axis: :y, side: :min }
        when HANDLE_RESIZE_MAX_Y then { type: :resize, axis: :y, side: :max }
        when HANDLE_RESIZE_MIN_Z then { type: :resize, axis: :z, side: :min }
        when HANDLE_RESIZE_MAX_Z then { type: :resize, axis: :z, side: :max }
        when HANDLE_MOVE_PLANE_XY then { type: :move_plane, axes: %i[x y], normal_axis: :z }
        when HANDLE_MOVE_PLANE_XZ then { type: :move_plane, axes: %i[x z], normal_axis: :y }
        when HANDLE_MOVE_PLANE_YZ then { type: :move_plane, axes: %i[y z], normal_axis: :x }
        when HANDLE_ROTATE_X then { type: :rotate, axis: :x, plane_axes: %i[y z] }
        when HANDLE_ROTATE_Y then { type: :rotate, axis: :y, plane_axes: %i[z x] }
        when HANDLE_ROTATE_Z then { type: :rotate, axis: :z, plane_axes: %i[x y] }
        when HANDLE_MOVE_CENTER then { type: :move_center }
        when HANDLE_SCALE_UNIFORM then { type: :scale_uniform }
        end
      end

      def handle_segment(handle_id, view = nil, state: nil)
        return nil unless MOVE_HANDLE_IDS.include?(handle_id)

        role = handle_role(handle_id)
        axis = role[:axis]
        face_center = axis_face_center(axis, :max, state)
        gap = screen_length_to_model(view, MOVE_GAP_PIXELS, face_center)
        length = screen_length_to_model(view, MOVE_LENGTH_PIXELS, face_center)
        axis_dir = axis_vector(axis, state)

        [offset_point(face_center, axis_dir, gap), offset_point(face_center, axis_dir, gap + length)]
      end

      def handle_point(handle_id, view = nil, state: nil)
        return nil unless box_defined_for_state?(state)

        role = handle_role(handle_id)
        return box_center(state) unless role

        case role[:type]
        when :move
          segment = handle_segment(handle_id, view, state: state)
          segment ? segment.last : box_center(state)
        when :resize
          axis_face_center(role[:axis], role[:side], state)
        when :move_plane
          plane_handle_definition(handle_id, view, state: state)[:center]
        when :rotate
          rotation_handle_definition(handle_id, view, state: state)[:midpoint]
        when :move_center, :scale_uniform
          box_center(state)
        end
      end

      def plane_handle_definition(handle_id, view = nil, state: nil)
        role = handle_role(handle_id)
        return nil unless role && role[:type] == :move_plane && box_defined_for_state?(state)

        current_state = state || snapshot
        center = box_center(current_state)
        offset = screen_length_to_model(view, PLANE_OFFSET_PIXELS, center)
        half_size = screen_length_to_model(view, PLANE_SIZE_PIXELS * 0.5, center)
        total_offset = offset + half_size
        axis_a = axis_vector(role[:axes][0], current_state)
        axis_b = axis_vector(role[:axes][1], current_state)
        plane_center = offset_point(offset_point(center, axis_a, total_offset), axis_b, total_offset)

        { center: plane_center, axis_a: axis_a, axis_b: axis_b, half_size: half_size }
      end

      def rotation_handle_definition(handle_id, view = nil, state: nil)
        role = handle_role(handle_id)
        return nil unless role && role[:type] == :rotate && box_defined_for_state?(state)

        current_state = state || snapshot
        center = box_center(current_state)
        axis_a_name, axis_b_name = role[:plane_axes]
        axis_a = axis_vector(axis_a_name, current_state)
        axis_b = axis_vector(axis_b_name, current_state)
        radius = screen_length_to_model(view, ROTATE_RADIUS_PIXELS, center)
        points = rotation_arc_points(center, axis_a, axis_b, radius)

        { center: center, axis_a: axis_a, axis_b: axis_b, radius: radius, points: points, midpoint: points[points.length / 2] }
      end

      def draw(view)
        draw_item_outline(view, hovered_item, hover: true) if hovered_item && hovered_item != selected_item && !RenderItemRegistry.supports_native_highlight?(hovered_item)
        return unless selected_item

        draw_item_outline(view, selected_item, selected: true) unless RenderItemRegistry.supports_native_highlight?(selected_item)
        draw_gizmo(view) unless native_gizmo_available?
      end

      private

      def interaction_operation_name(context)
        role = context && context[:role]
        case role && role[:type]
        when :move, :move_plane, :move_center
          'Move Gaussian Object'
        when :resize, :scale_uniform
          'Scale Gaussian Object'
        when :rotate
          'Rotate Gaussian Object'
        else
          'Transform Gaussian Object'
        end
      end

      def sync_item_highlights!
        return unless defined?(RenderItemRegistry)

        RenderItemRegistry.apply_highlights(
          hovered_id: @hovered_item_id,
          selected_id: @selected_item_id
        )
      end

      def refresh_native_state!
        return unless defined?(GaussianPoints::OverlayBridgeNative)

        item = selected_item
        GaussianPoints::OverlayBridgeNative.sync_move_tool_box(
          enabled: tool_active? && !item.nil?,
          visible: !item.nil?,
          gizmo_visible: tool_active? && !item.nil?,
          center_scale_mode: center_handle_scale_mode?,
          center_point: item&.dig(:box_state, :center),
          half_extents: item&.dig(:box_state, :half_extents),
          axes: item&.dig(:box_state, :axes),
          hovered_handle: hovered_handle,
          active_handle: active_handle
        )
      end

      def center_handle_scale_mode?
        handle_id = active_handle != HANDLE_NONE ? active_handle : hovered_handle
        handle_id == HANDLE_SCALE_UNIFORM
      end

      def native_gizmo_available?
        defined?(GaussianPoints::OverlayBridgeNative) &&
          GaussianPoints::OverlayBridgeNative.respond_to?(:move_tool_visual_available?) &&
          GaussianPoints::OverlayBridgeNative.move_tool_visual_available?
      end

      def draw_item_outline(view, item, hover: false, selected: false)
        points = RenderItemRegistry.world_corners(item)
        edge_points(points).each do |segment|
          view.line_width = selected ? 3 : 2
          view.drawing_color =
            if selected
              Sketchup::Color.new(255, 196, 96, 255)
            elsif hover
              Sketchup::Color.new(255, 220, 140, 230)
            else
              Sketchup::Color.new(255, 170, 40, 180)
            end
          view.draw(GL_LINES, segment)
        end
      end

      def draw_gizmo(view)
        MOVE_HANDLE_IDS.each do |handle_id|
          segment = handle_segment(handle_id, view)
          next unless segment

          color = handle_color(handle_id)
          highlighted = hovered_handle == handle_id || active_handle == handle_id
          draw_color = highlighted ? brighten(color) : color
          view.line_width = highlighted ? 4 : 2
          view.drawing_color = draw_color
          view.draw(GL_LINES, segment)
          view.draw_points([segment.last], highlighted ? 14 : 10, 3, draw_color)
        end

        PLANE_HANDLE_IDS.each do |handle_id|
          definition = plane_handle_definition(handle_id, view)
          next unless definition

          highlighted = hovered_handle == handle_id || active_handle == handle_id
          points = plane_disc_points(definition)
          view.drawing_color = faded(handle_color(handle_id), highlighted ? 110 : 70)
          view.draw(GL_POLYGON, points)
          view.line_width = highlighted ? 3 : 1
          view.drawing_color = highlighted ? brighten(handle_color(handle_id)) : handle_color(handle_id)
          view.draw(GL_LINE_LOOP, points)
        end

        ROTATE_HANDLE_IDS.each do |handle_id|
          definition = rotation_handle_definition(handle_id, view)
          next unless definition

          highlighted = hovered_handle == handle_id || active_handle == handle_id
          view.line_width = highlighted ? 4 : 2
          view.drawing_color = highlighted ? brighten(handle_color(handle_id)) : handle_color(handle_id)
          view.draw(GL_LINE_STRIP, definition[:points])
        end

        center_highlighted = CENTER_HANDLE_IDS.include?(hovered_handle) || CENTER_HANDLE_IDS.include?(active_handle)
        center_color = center_highlighted ? brighten(handle_color(HANDLE_MOVE_CENTER)) : handle_color(HANDLE_MOVE_CENTER)
        center_point = box_center
        center_size = center_highlighted ? 16 : 12
        view.drawing_color = center_color
        view.draw_points([center_point], center_size, 2, center_color)

        RESIZE_HANDLE_IDS.each do |handle_id|
          highlighted = hovered_handle == handle_id || active_handle == handle_id
          point = handle_point(handle_id, view)
          next unless point

          size = highlighted ? 16 : 12
          draw_color = highlighted ? brighten(handle_color(handle_id)) : handle_color(handle_id)
          view.drawing_color = draw_color
          view.draw_points([point], size, 1, draw_color)
        end
      end

      def rotation_arc_points(center, axis_a, axis_b, radius)
        start_angle = (Math::PI * 0.25) - (ROTATE_SWEEP * 0.5)
        Array.new(ROTATE_SEGMENTS + 1) do |index|
          angle = start_angle + ((index.to_f / ROTATE_SEGMENTS) * ROTATE_SWEEP)
          point = offset_point(center, axis_a, Math.cos(angle) * radius)
          offset_point(point, axis_b, Math.sin(angle) * radius)
        end
      end

      def plane_disc_points(definition)
        center = definition[:center]
        axis_a = definition[:axis_a]
        axis_b = definition[:axis_b]
        half_size = definition[:half_size]
        segments = 20

        Array.new(segments) do |index|
          angle = (index.to_f / segments) * Math::PI * 2.0
          point = offset_point(center, axis_a, Math.cos(angle) * half_size)
          offset_point(point, axis_b, Math.sin(angle) * half_size)
        end
      end

      def edge_points(points)
        [
          [points[0], points[1]], [points[1], points[2]], [points[2], points[3]], [points[3], points[0]],
          [points[4], points[5]], [points[5], points[6]], [points[6], points[7]], [points[7], points[4]],
          [points[0], points[4]], [points[1], points[5]], [points[2], points[6]], [points[3], points[7]]
        ]
      end

      def ray_intersection_distance(state, ray)
        origin, direction = ray
        center = state[:center]
        axes = %i[x y z].map { |axis| axis_vector(axis, state) }
        half_extents = %i[x y z].map { |axis| half_extent(axis, state) }
        local_origin = origin - center
        local_origin_values = axes.map { |axis| local_origin.dot(axis) }
        local_direction_values = axes.map { |axis| direction.dot(axis) }

        t_min = -Float::INFINITY
        t_max = Float::INFINITY

        3.times do |index|
          origin_component = local_origin_values[index]
          dir_component = local_direction_values[index]
          extent = half_extents[index]

          if dir_component.abs < 1.0e-8
            return nil if origin_component.abs > extent
            next
          end

          t1 = (-extent - origin_component) / dir_component
          t2 = ( extent - origin_component) / dir_component
          t_near, t_far = [t1, t2].minmax
          t_min = [t_min, t_near].max
          t_max = [t_max, t_far].min
          return nil if t_min > t_max
        end

        return nil if t_max < 0.0
        t_min >= 0.0 ? t_min : t_max
      end

      def box_defined_for_state?(state)
        current = state || snapshot
        current && current[:center] && current[:half_extents] && current[:axes]
      end

      def axis_face_center(axis, side, state = nil)
        current_state = state || snapshot
        direction = axis_vector(axis, current_state)
        distance = half_extent(axis, current_state) * (side == :max ? 1.0 : -1.0)
        offset_point(box_center(current_state), direction, distance)
      end

      def normalize_snapshot(state)
        axes = state[:axes]
        x = normalize_vector(axes[:x], :x)
        y_seed = axes[:y] || Geom::Vector3d.new(0, 1, 0)
        y = y_seed - project_vector(y_seed, x)
        y = normalize_vector(y, :y)
        z = normalize_vector(x.cross(y), :z)
        z.reverse! if axes[:z] && z.dot(axes[:z]) < 0
        y = normalize_vector(z.cross(x), :y)

        {
          center: state[:center].clone,
          half_extents: {
            x: [state[:half_extents][:x].to_f, MIN_BOX_SIZE * 0.5].max,
            y: [state[:half_extents][:y].to_f, MIN_BOX_SIZE * 0.5].max,
            z: [state[:half_extents][:z].to_f, MIN_BOX_SIZE * 0.5].max
          },
          axes: { x: x, y: y, z: z }
        }
      end

      def project_vector(vector, onto)
        scaled_vector(onto, vector.dot(onto))
      end

      def normalize_vector(vector, fallback_axis = :x)
        fallback =
          case fallback_axis
          when :x then Geom::Vector3d.new(1, 0, 0)
          when :y then Geom::Vector3d.new(0, 1, 0)
          else Geom::Vector3d.new(0, 0, 1)
          end
        axis = (vector || fallback).clone
        return fallback if axis.length <= 0.0001

        axis.normalize!
        axis
      rescue StandardError
        fallback
      end

      def scaled_vector(vector, length)
        scaled = normalize_vector(vector).clone
        scaled.length = length.abs
        scaled.reverse! if length < 0.0
        scaled
      end

      def screen_length_to_model(view, pixels, point = nil)
        point ||= box_center
        return 100.mm if !view || !view.respond_to?(:pixels_to_model)

        view.pixels_to_model(pixels, point).to_f
      rescue StandardError
        100.mm
      end

      def offset_point(point, direction, distance)
        point.offset(scaled_vector(direction, distance))
      end

      def normalize_handle(handle_id)
        HANDLE_IDS.include?(handle_id) ? handle_id : HANDLE_NONE
      end

      def uniform_scale_factor(state, delta)
        values = state[:half_extents].values.map { |value| [value.to_f, MIN_BOX_SIZE * 0.5].max }
        reference = values.max
        return 1.0 if reference <= 1.0e-8

        raw_factor = (reference + delta.to_f) / reference
        min_factor = values.map { |value| (MIN_BOX_SIZE * 0.5) / value }.max
        [raw_factor, min_factor].max
      end

      def handle_color(handle_id)
        case handle_id
        when HANDLE_MOVE_X, HANDLE_RESIZE_MIN_X, HANDLE_RESIZE_MAX_X, HANDLE_ROTATE_X
          Sketchup::Color.new(220, 70, 55)
        when HANDLE_MOVE_Y, HANDLE_RESIZE_MIN_Y, HANDLE_RESIZE_MAX_Y, HANDLE_ROTATE_Y
          Sketchup::Color.new(70, 190, 90)
        when HANDLE_MOVE_Z, HANDLE_RESIZE_MIN_Z, HANDLE_RESIZE_MAX_Z, HANDLE_ROTATE_Z
          Sketchup::Color.new(70, 120, 230)
        when HANDLE_MOVE_PLANE_XY
          Sketchup::Color.new(232, 164, 52)
        when HANDLE_MOVE_PLANE_XZ
          Sketchup::Color.new(188, 96, 188)
        when HANDLE_MOVE_PLANE_YZ
          Sketchup::Color.new(74, 182, 196)
        else
          Sketchup::Color.new(255, 196, 96)
        end
      end

      def brighten(color)
        Sketchup::Color.new(
          [color.red + 35, 255].min,
          [color.green + 35, 255].min,
          [color.blue + 35, 255].min,
          color.alpha
        )
      end

      def faded(color, alpha)
        Sketchup::Color.new(color.red, color.green, color.blue, alpha)
      end

      def invalidate_view
        refresh_native_state!
        Sketchup.active_model&.active_view&.invalidate
      end
    end
  end
end
