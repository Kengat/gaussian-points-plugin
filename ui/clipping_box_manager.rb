module GaussianPoints
  module UIparts
    module ClippingManager
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

      MOVE_HANDLE_IDS = [
        HANDLE_MOVE_X,
        HANDLE_MOVE_Y,
        HANDLE_MOVE_Z
      ].freeze

      RESIZE_HANDLE_IDS = [
        HANDLE_RESIZE_MIN_X,
        HANDLE_RESIZE_MAX_X,
        HANDLE_RESIZE_MIN_Y,
        HANDLE_RESIZE_MAX_Y,
        HANDLE_RESIZE_MIN_Z,
        HANDLE_RESIZE_MAX_Z
      ].freeze

      PLANE_HANDLE_IDS = [
        HANDLE_MOVE_PLANE_XY,
        HANDLE_MOVE_PLANE_XZ,
        HANDLE_MOVE_PLANE_YZ
      ].freeze

      ROTATE_HANDLE_IDS = [
        HANDLE_ROTATE_X,
        HANDLE_ROTATE_Y,
        HANDLE_ROTATE_Z
      ].freeze

      CENTER_HANDLE_IDS = [
        HANDLE_MOVE_CENTER,
        HANDLE_SCALE_UNIFORM
      ].freeze

      HANDLE_IDS = (MOVE_HANDLE_IDS + RESIZE_HANDLE_IDS + PLANE_HANDLE_IDS + ROTATE_HANDLE_IDS + CENTER_HANDLE_IDS).freeze

      BOX_PADDING_RATIO = 0.05
      MIN_PADDING = 25.mm
      MIN_BOX_SIZE = 50.mm
      DEFAULT_HALF_SIZE = 500.mm

      MOVE_GAP_PIXELS = 18.0
      MOVE_LENGTH_PIXELS = 54.0
      PLANE_OFFSET_PIXELS = 24.0
      PLANE_SIZE_PIXELS = 18.0
      CENTER_HANDLE_PIXELS = 12.0
      RESIZE_SIZE_PIXELS = 12.0
      ROTATE_CENTER_OFFSET_PIXELS = 0.0
      ROTATE_RADIUS_PIXELS = 120.0
      ROTATE_SEGMENTS = 24
      ROTATE_SWEEP = Math::PI * 0.32

      def active?
        @active == true
      end

      def box_visible?
        @box_visible != false
      end

      def hovered_handle
        @hovered_handle || HANDLE_NONE
      end

      def active_handle
        @active_handle || HANDLE_NONE
      end

      def gizmo_enabled?
        @gizmo_enabled == true
      end

      def gizmo_visible?
        active? && box_visible? && box_defined? && gizmo_enabled?
      end

      def box_defined?
        !@box_center.nil? && !@half_extents.nil? && !@box_axes.nil?
      end

      def box_center(state = nil)
        center = state ? state[:center] : @box_center
        center ? center.clone : Geom::Point3d.new(0, 0, 0)
      end

      def box_min
        axis_aligned_bounds[:min]
      end

      def box_max
        axis_aligned_bounds[:max]
      end

      def box_dimensions(state = nil)
        half_extents = state ? state[:half_extents] : @half_extents
        return Geom::Vector3d.new(MIN_BOX_SIZE, MIN_BOX_SIZE, MIN_BOX_SIZE) unless half_extents

        Geom::Vector3d.new(
          half_extents[:x] * 2.0,
          half_extents[:y] * 2.0,
          half_extents[:z] * 2.0
        )
      end

      def axis_vector(axis, state = nil)
        axes = state ? state[:axes] : @box_axes
        vector = axes && axes[axis]
        vector ? normalize_vector(vector, axis) : world_axis(axis)
      end

      def half_extent(axis, state = nil)
        half_extents = state ? state[:half_extents] : @half_extents
        half_extents ? half_extents[axis].to_f : DEFAULT_HALF_SIZE.to_f
      end

      def snapshot
        return nil unless box_defined?

        {
          center: @box_center.clone,
          half_extents: @half_extents.transform_values(&:to_f),
          axes: @box_axes.transform_values(&:clone)
        }
      end

      def apply_snapshot(state, refresh: true)
        return unless state

        @box_center = state[:center].clone
        @half_extents = normalized_half_extents(state[:half_extents])
        @box_axes = normalized_axes(state[:axes])
        refresh_clipping if refresh
      end

      def set_box_extents(min_point, max_point, refresh: true)
        min_point = Geom::Point3d.new(min_point.x, min_point.y, min_point.z)
        max_point = Geom::Point3d.new(max_point.x, max_point.y, max_point.z)
        center = Geom::Point3d.new(
          (min_point.x + max_point.x) * 0.5,
          (min_point.y + max_point.y) * 0.5,
          (min_point.z + max_point.z) * 0.5
        )
        half_extents = {
          x: [(max_point.x - min_point.x).abs * 0.5, MIN_BOX_SIZE * 0.5].max,
          y: [(max_point.y - min_point.y).abs * 0.5, MIN_BOX_SIZE * 0.5].max,
          z: [(max_point.z - min_point.z).abs * 0.5, MIN_BOX_SIZE * 0.5].max
        }
        apply_snapshot(
          {
            center: center,
            half_extents: half_extents,
            axes: default_axes
          },
          refresh: refresh
        )
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
        new_half_extents = state[:half_extents].transform_values do |value|
          [value.to_f + delta.to_f, MIN_BOX_SIZE * 0.5].max
        end

        {
          center: state[:center].clone,
          half_extents: new_half_extents,
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

      def toggle_clip
        active? ? disable_clip : enable_clip
      end

      def enable_clip(activate_tool: false)
        was_active = active?
        fit_to_scene_bounds unless box_defined?
        @active = true
        @box_visible = true
        @gizmo_enabled = activate_tool ? true : false unless was_active
        refresh_clipping
        self.activate_tool if activate_tool
        true
      end

      def activate_tool
        enable_clip(activate_tool: false) unless active?
        @gizmo_enabled = true
        select_editor_tool
        refresh_native_state!
        invalidate_view
        true
      end

      def toggle_gizmo
        enable_clip(activate_tool: false) unless active?
        @gizmo_enabled = !gizmo_enabled?
        clear_interaction_state(sync: false)

        if gizmo_visible?
          select_editor_tool
        else
          release_editor_tool
        end

        refresh_native_state!
        invalidate_view
      end

      def disable_clip
        return false unless active? || box_defined?

        @active = false
        @gizmo_enabled = false
        release_editor_tool
        clear_interaction_state(sync: false)
        refresh_clipping
        true
      end

      def remove_clip
        disable_clip
        @box_center = nil
        @half_extents = nil
        @box_axes = nil
        refresh_native_state!
        invalidate_view
      end

      def toggle_box_visibility
        @box_visible = !box_visible?
        clear_interaction_state(sync: false)
        if gizmo_visible?
          select_editor_tool
        else
          release_editor_tool
        end
        refresh_native_state!
        invalidate_view
      end

      def reset_box_position
        fit_to_scene_bounds
        refresh_clipping
      end

      def apply_clipping
        refresh_clipping
      end

      def point_inside?(point)
        return true unless active? && box_defined?
        return false unless point

        local = point - @box_center
        projected_axis_value(local, :x) <= @half_extents[:x] &&
          projected_axis_value(local, :y) <= @half_extents[:y] &&
          projected_axis_value(local, :z) <= @half_extents[:z]
      end

      def status_label
        return 'Clip box: disabled' unless active?

        "Clip box: #{box_visible? ? 'visible' : 'hidden'}, gizmo: #{gizmo_enabled? ? 'enabled' : 'disabled'}"
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
        return nil unless box_defined_for_state?(state)

        role = handle_role(handle_id)
        axis = role[:axis]
        face_center = axis_face_center(axis, :max, state)
        gap = screen_length_to_model(view, MOVE_GAP_PIXELS, face_center)
        length = screen_length_to_model(view, MOVE_LENGTH_PIXELS, face_center)
        axis_dir = axis_vector(axis, state)

        [
          offset_point(face_center, axis_dir, gap),
          offset_point(face_center, axis_dir, gap + length)
        ]
      end

      def handle_point(handle_id, view = nil, state: nil)
        return Geom::Point3d.new(0, 0, 0) unless box_defined_for_state?(state)

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
        else
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

        {
          center: plane_center,
          axis_a: axis_a,
          axis_b: axis_b,
          half_size: half_size
        }
      end

      def rotation_handle_definition(handle_id, view = nil, state: nil)
        role = handle_role(handle_id)
        return nil unless role && role[:type] == :rotate && box_defined_for_state?(state)

        current_state = state || snapshot
        box_origin = box_center(current_state)
        axis_a_name, axis_b_name = role[:plane_axes]
        axis_a = axis_vector(axis_a_name, current_state)
        axis_b = axis_vector(axis_b_name, current_state)
        offset = screen_length_to_model(view, ROTATE_CENTER_OFFSET_PIXELS, box_origin)
        radius = screen_length_to_model(view, ROTATE_RADIUS_PIXELS, box_origin)
        center = box_origin
        points = rotation_arc_points(center, axis_a, axis_b, radius)

        {
          center: center,
          axis_a: axis_a,
          axis_b: axis_b,
          radius: radius,
          points: points,
          midpoint: points[points.length / 2]
        }
      end

      def set_hovered_handle(handle_id)
        handle_id = normalize_handle(handle_id)
        return if hovered_handle == handle_id

        @hovered_handle = handle_id
        refresh_native_state!
        invalidate_view
      end

      def set_active_handle(handle_id)
        handle_id = normalize_handle(handle_id)
        return if active_handle == handle_id

        @active_handle = handle_id
        refresh_native_state!
        invalidate_view
      end

      def clear_interaction_state(sync: true)
        @hovered_handle = HANDLE_NONE
        @active_handle = HANDLE_NONE
        refresh_native_state! if sync
      end

      def center_handle_size(view = nil)
        screen_length_to_model(view, CENTER_HANDLE_PIXELS * 0.5, box_center)
      end

      def draw_fallback(view)
        return unless active? && box_visible? && box_defined?

        faces.each do |points|
          view.drawing_color = Sketchup::Color.new(255, 166, 0, 20)
          view.draw(GL_QUADS, points)
        end

        view.line_width = 2
        view.drawing_color = Sketchup::Color.new(255, 170, 40, 220)
        view.draw(GL_LINES, edges.flatten)

        draw_gizmo(view)
      end

      private

      def refresh_clipping
        overlay = GaussianPoints.overlay
        overlay.refresh_display_points if overlay && overlay.respond_to?(:refresh_display_points)
        sync_scene_bounds_proxy!
        refresh_native_state!
        invalidate_view
      end

      def refresh_native_state!
        return unless defined?(GaussianPoints::OverlayBridgeNative)

        GaussianPoints::OverlayBridgeNative.sync_clip_box(
          enabled: active? && box_defined?,
          visible: active? && box_visible? && box_defined?,
          gizmo_visible: gizmo_visible?,
          center_scale_mode: center_handle_scale_mode?,
          center_point: @box_center,
          half_extents: @half_extents,
          axes: @box_axes,
          hovered_handle: hovered_handle,
          active_handle: active_handle
        )
      end

      def center_handle_scale_mode?
        handle_id = active_handle != HANDLE_NONE ? active_handle : hovered_handle
        handle_id == HANDLE_SCALE_UNIFORM
      end

      def fit_to_scene_bounds
        bounds = combined_scene_bounds

        if bounds.nil? || bounds.empty?
          @box_center = Geom::Point3d.new(0, 0, 0)
          @half_extents = { x: DEFAULT_HALF_SIZE, y: DEFAULT_HALF_SIZE, z: DEFAULT_HALF_SIZE }
          @box_axes = default_axes
          return
        end

        padding = [bounds.diagonal * BOX_PADDING_RATIO, MIN_PADDING].max
        min = bounds.min
        max = bounds.max
        @box_center = Geom::Point3d.new(
          (min.x + max.x) * 0.5,
          (min.y + max.y) * 0.5,
          (min.z + max.z) * 0.5
        )
        @half_extents = {
          x: [((max.x - min.x) * 0.5) + padding, MIN_BOX_SIZE * 0.5].max,
          y: [((max.y - min.y) * 0.5) + padding, MIN_BOX_SIZE * 0.5].max,
          z: [((max.z - min.z) * 0.5) + padding, MIN_BOX_SIZE * 0.5].max
        }
        @box_axes = default_axes
      end

      def combined_scene_bounds
        bb = Geom::BoundingBox.new

        if defined?(GaussianPoints::SceneBoundsProxy) &&
           GaussianPoints::SceneBoundsProxy.respond_to?(:current_bounds_snapshot)
          snapshot = GaussianPoints::SceneBoundsProxy.current_bounds_snapshot
          if snapshot
            bb.add(Geom::Point3d.new(*snapshot[:min]))
            bb.add(Geom::Point3d.new(*snapshot[:max]))
          end
        end

        bb.empty? ? nil : bb
      end

      def normalize_handle(handle_id)
        HANDLE_IDS.include?(handle_id) ? handle_id : HANDLE_NONE
      end

      def box_defined_for_state?(state)
        if state
          state[:center] && state[:half_extents] && state[:axes]
        else
          box_defined?
        end
      end

      def default_axes
        {
          x: Geom::Vector3d.new(1, 0, 0),
          y: Geom::Vector3d.new(0, 1, 0),
          z: Geom::Vector3d.new(0, 0, 1)
        }
      end

      def world_axis(axis)
        default_axes[axis] || Geom::Vector3d.new(0, 0, 0)
      end

      def normalized_half_extents(half_extents)
        {
          x: [half_extents[:x].to_f, MIN_BOX_SIZE * 0.5].max,
          y: [half_extents[:y].to_f, MIN_BOX_SIZE * 0.5].max,
          z: [half_extents[:z].to_f, MIN_BOX_SIZE * 0.5].max
        }
      end

      def normalized_axes(axes)
        x = normalize_vector(axes[:x], :x)
        y_seed = axes[:y] || world_axis(:y)
        y = y_seed - project_vector(y_seed, x)
        y = normalize_vector(y, :y)
        z = normalize_vector(x.cross(y), :z)
        z.reverse! if axes[:z] && z.dot(axes[:z]) < 0
        y = normalize_vector(z.cross(x), :y)
        { x: x, y: y, z: z }
      end

      def normalize_vector(vector, fallback_axis = :x)
        clone = (vector || world_axis(fallback_axis)).clone
        return world_axis(fallback_axis) if clone.length <= 0.0001

        clone.normalize!
        clone
      rescue StandardError
        world_axis(fallback_axis)
      end

      def scaled_vector(vector, length)
        scaled = normalize_vector(vector).clone
        scaled.length = length.abs
        scaled.reverse! if length < 0.0
        scaled
      end

      def project_vector(vector, onto)
        scaled_vector(onto, vector.dot(onto))
      end

      def projected_axis_value(vector, axis)
        vector.dot(axis_vector(axis)).abs
      end

      def axis_face_center(axis, side, state = nil)
        current_state = state || snapshot
        direction = axis_vector(axis, current_state)
        distance = half_extent(axis, current_state) * (side == :max ? 1.0 : -1.0)
        offset_point(box_center(current_state), direction, distance)
      end

      def screen_length_to_model(view, pixels, point = nil)
        point ||= box_center
        return fallback_screen_length(pixels) unless view && view.respond_to?(:pixels_to_model)

        view.pixels_to_model(pixels, point).to_f
      rescue StandardError
        fallback_screen_length(pixels)
      end

      def fallback_screen_length(pixels)
        base = gizmo_extension_length.to_f / MOVE_LENGTH_PIXELS
        pixels * base
      end

      def offset_point(point, direction, distance)
        point.offset(scaled_vector(direction, distance))
      end

      def gizmo_extension_length
        dims = box_dimensions
        largest = [dims.x.abs, dims.y.abs, dims.z.abs].max
        [[largest * 0.18, MIN_BOX_SIZE].max, 500.mm].min
      end

      def axis_aligned_bounds
        return { min: Geom::Point3d.new(0, 0, 0), max: Geom::Point3d.new(0, 0, 0) } unless box_defined?

        bb = Geom::BoundingBox.new
        corners.each { |corner| bb.add(corner) }
        { min: bb.min, max: bb.max }
      end

      def corners(state = nil)
        current_state = state || snapshot
        center = box_center(current_state)
        axis_x = axis_vector(:x, current_state)
        axis_y = axis_vector(:y, current_state)
        axis_z = axis_vector(:z, current_state)
        half_x = half_extent(:x, current_state)
        half_y = half_extent(:y, current_state)
        half_z = half_extent(:z, current_state)

        [
          oriented_corner(center, axis_x, axis_y, axis_z, -half_x, -half_y, -half_z),
          oriented_corner(center, axis_x, axis_y, axis_z,  half_x, -half_y, -half_z),
          oriented_corner(center, axis_x, axis_y, axis_z,  half_x,  half_y, -half_z),
          oriented_corner(center, axis_x, axis_y, axis_z, -half_x,  half_y, -half_z),
          oriented_corner(center, axis_x, axis_y, axis_z, -half_x, -half_y,  half_z),
          oriented_corner(center, axis_x, axis_y, axis_z,  half_x, -half_y,  half_z),
          oriented_corner(center, axis_x, axis_y, axis_z,  half_x,  half_y,  half_z),
          oriented_corner(center, axis_x, axis_y, axis_z, -half_x,  half_y,  half_z)
        ]
      end

      def oriented_corner(center, axis_x, axis_y, axis_z, dx, dy, dz)
        point = center.clone
        point = offset_point(point, axis_x, dx)
        point = offset_point(point, axis_y, dy)
        offset_point(point, axis_z, dz)
      end

      def edges
        pts = corners
        [
          [pts[0], pts[1]], [pts[1], pts[2]], [pts[2], pts[3]], [pts[3], pts[0]],
          [pts[4], pts[5]], [pts[5], pts[6]], [pts[6], pts[7]], [pts[7], pts[4]],
          [pts[0], pts[4]], [pts[1], pts[5]], [pts[2], pts[6]], [pts[3], pts[7]]
        ]
      end

      def faces
        pts = corners
        [
          [pts[0], pts[1], pts[2], pts[3]],
          [pts[4], pts[5], pts[6], pts[7]],
          [pts[0], pts[1], pts[5], pts[4]],
          [pts[1], pts[2], pts[6], pts[5]],
          [pts[2], pts[3], pts[7], pts[6]],
          [pts[3], pts[0], pts[4], pts[7]]
        ]
      end

      def rotation_arc_points(center, axis_a, axis_b, radius)
        start_angle = (Math::PI * 0.25) - (ROTATE_SWEEP * 0.5)
        Array.new(ROTATE_SEGMENTS + 1) do |index|
          angle = start_angle + ((index.to_f / ROTATE_SEGMENTS) * ROTATE_SWEEP)
          point = offset_point(center, axis_a, Math.cos(angle) * radius)
          offset_point(point, axis_b, Math.sin(angle) * radius)
        end
      end

      def draw_gizmo(view)
        MOVE_HANDLE_IDS.each do |handle_id|
          segment = handle_segment(handle_id, view)
          next unless segment

          color = handle_color(handle_id)
          highlighted = hovered_handle == handle_id || active_handle == handle_id
          view.line_width = highlighted ? 4 : 2
          view.drawing_color = highlighted ? brighten(color) : color
          view.draw(GL_LINES, segment)
          view.draw_points([segment.last], highlighted ? 14 : 10, 3, view.drawing_color)
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
          size = highlighted ? 16 : 12
          view.drawing_color = highlighted ? brighten(handle_color(handle_id)) : handle_color(handle_id)
          view.draw_points([point], size, 1, view.drawing_color)
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
        when HANDLE_MOVE_CENTER, HANDLE_SCALE_UNIFORM
          Sketchup::Color.new(255, 196, 96)
        else
          Sketchup::Color.new(255, 170, 40)
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
        Sketchup.active_model&.active_view&.invalidate
      end

      def sync_scene_bounds_proxy!
        return unless defined?(GaussianPoints::SceneBoundsProxy)

        if active? && box_visible? && box_defined?
          GaussianPoints::SceneBoundsProxy.update_clip_box_points(corners)
        else
          GaussianPoints::SceneBoundsProxy.clear_clip_box
        end
      end

      def select_editor_tool
        Sketchup.active_model&.select_tool(ClippingBoxTool.new)
      end

      def release_editor_tool
        Sketchup.active_model&.select_tool(nil)
      end
    end
  end
end
