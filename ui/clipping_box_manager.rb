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

      HANDLE_IDS = (MOVE_HANDLE_IDS + RESIZE_HANDLE_IDS + PLANE_HANDLE_IDS).freeze

      BOX_PADDING_RATIO = 0.05
      MIN_PADDING = 25.mm
      MIN_BOX_SIZE = 50.mm
      DEFAULT_HALF_SIZE = 500.mm

      MOVE_GAP_PIXELS = 18.0
      MOVE_LENGTH_PIXELS = 54.0
      PLANE_OFFSET_PIXELS = 16.0
      PLANE_SIZE_PIXELS = 18.0
      RESIZE_SIZE_PIXELS = 12.0

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

      def box_min
        @box_min&.clone
      end

      def box_max
        @box_max&.clone
      end

      def box_defined?
        !@box_min.nil? && !@box_max.nil?
      end

      def toggle_clip
        active? ? disable_clip : enable_clip
      end

      def gizmo_enabled?
        @gizmo_enabled == true
      end

      def gizmo_visible?
        active? && box_visible? && box_defined? && gizmo_enabled?
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
        @box_min = nil
        @box_max = nil
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

        point.x >= @box_min.x && point.x <= @box_max.x &&
          point.y >= @box_min.y && point.y <= @box_max.y &&
          point.z >= @box_min.z && point.z <= @box_max.z
      end

      def status_label
        return 'Clip box: disabled' unless active?

        "Clip box: #{box_visible? ? 'visible' : 'hidden'}, gizmo: #{gizmo_enabled? ? 'enabled' : 'disabled'}"
      end

      def box_center
        return Geom::Point3d.new(0, 0, 0) unless box_defined?

        Geom::Point3d.new(
          (@box_min.x + @box_max.x) * 0.5,
          (@box_min.y + @box_max.y) * 0.5,
          (@box_min.z + @box_max.z) * 0.5
        )
      end

      def box_dimensions
        return Geom::Vector3d.new(MIN_BOX_SIZE, MIN_BOX_SIZE, MIN_BOX_SIZE) unless box_defined?

        Geom::Vector3d.new(
          @box_max.x - @box_min.x,
          @box_max.y - @box_min.y,
          @box_max.z - @box_min.z
        )
      end

      def axis_vector(axis)
        case axis
        when :x then Geom::Vector3d.new(1, 0, 0)
        when :y then Geom::Vector3d.new(0, 1, 0)
        when :z then Geom::Vector3d.new(0, 0, 1)
        else Geom::Vector3d.new(0, 0, 0)
        end
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
        end
      end

      def handle_segment(handle_id, view = nil)
        return nil unless MOVE_HANDLE_IDS.include?(handle_id) && box_defined?

        role = handle_role(handle_id)
        axis = role[:axis]
        face_center = axis_face_center(axis, :max)
        gap = screen_length_to_model(view, MOVE_GAP_PIXELS, face_center)
        length = screen_length_to_model(view, MOVE_LENGTH_PIXELS, face_center)
        axis_dir = axis_vector(axis)

        [
          offset_point(face_center, axis_dir, gap),
          offset_point(face_center, axis_dir, gap + length)
        ]
      end

      def handle_point(handle_id, view = nil)
        return Geom::Point3d.new(0, 0, 0) unless box_defined?

        center = box_center

        case handle_id
        when *MOVE_HANDLE_IDS
          segment = handle_segment(handle_id, view)
          segment ? segment.last : center
        when HANDLE_RESIZE_MIN_X
          Geom::Point3d.new(@box_min.x, center.y, center.z)
        when HANDLE_RESIZE_MAX_X
          Geom::Point3d.new(@box_max.x, center.y, center.z)
        when HANDLE_RESIZE_MIN_Y
          Geom::Point3d.new(center.x, @box_min.y, center.z)
        when HANDLE_RESIZE_MAX_Y
          Geom::Point3d.new(center.x, @box_max.y, center.z)
        when HANDLE_RESIZE_MIN_Z
          Geom::Point3d.new(center.x, center.y, @box_min.z)
        when HANDLE_RESIZE_MAX_Z
          Geom::Point3d.new(center.x, center.y, @box_max.z)
        when *PLANE_HANDLE_IDS
          plane_handle_definition(handle_id, view)[:center]
        else
          center
        end
      end

      def plane_handle_definition(handle_id, view = nil)
        role = handle_role(handle_id)
        return nil unless role && role[:type] == :move_plane && box_defined?

        center = box_center
        offset = screen_length_to_model(view, PLANE_OFFSET_PIXELS, center)
        half_size = screen_length_to_model(view, PLANE_SIZE_PIXELS * 0.5, center)
        total_offset = offset + half_size
        axis_a = axis_vector(role[:axes][0])
        axis_b = axis_vector(role[:axes][1])
        plane_center = offset_point(offset_point(center, axis_a, total_offset), axis_b, total_offset)

        {
          center: plane_center,
          axis_a: axis_a,
          axis_b: axis_b,
          half_size: half_size
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

      def set_box_extents(min_point, max_point, refresh: true)
        @box_min = Geom::Point3d.new(min_point.x, min_point.y, min_point.z)
        @box_max = Geom::Point3d.new(max_point.x, max_point.y, max_point.z)
        clamp_box_size!
        refresh_clipping if refresh
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
          min_point: @box_min,
          max_point: @box_max,
          hovered_handle: hovered_handle,
          active_handle: active_handle
        )
      end

      def fit_to_scene_bounds
        bounds = combined_scene_bounds

        if bounds.nil? || bounds.empty?
          @box_min = Geom::Point3d.new(-DEFAULT_HALF_SIZE, -DEFAULT_HALF_SIZE, -DEFAULT_HALF_SIZE)
          @box_max = Geom::Point3d.new(DEFAULT_HALF_SIZE, DEFAULT_HALF_SIZE, DEFAULT_HALF_SIZE)
          return
        end

        padding = [bounds.diagonal * BOX_PADDING_RATIO, MIN_PADDING].max
        min = bounds.min
        max = bounds.max
        @box_min = Geom::Point3d.new(min.x - padding, min.y - padding, min.z - padding)
        @box_max = Geom::Point3d.new(max.x + padding, max.y + padding, max.z + padding)
        clamp_box_size!
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

      def clamp_box_size!
        min = @box_min
        max = @box_max
        center = Geom::Point3d.new(
          (min.x + max.x) * 0.5,
          (min.y + max.y) * 0.5,
          (min.z + max.z) * 0.5
        )

        x_half = [(max.x - min.x) * 0.5, MIN_BOX_SIZE * 0.5].max
        y_half = [(max.y - min.y) * 0.5, MIN_BOX_SIZE * 0.5].max
        z_half = [(max.z - min.z) * 0.5, MIN_BOX_SIZE * 0.5].max

        @box_min = Geom::Point3d.new(center.x - x_half, center.y - y_half, center.z - z_half)
        @box_max = Geom::Point3d.new(center.x + x_half, center.y + y_half, center.z + z_half)
      end

      def normalize_handle(handle_id)
        HANDLE_IDS.include?(handle_id) ? handle_id : HANDLE_NONE
      end

      def axis_face_center(axis, side)
        center = box_center
        case [axis, side]
        when [:x, :min] then Geom::Point3d.new(@box_min.x, center.y, center.z)
        when [:x, :max] then Geom::Point3d.new(@box_max.x, center.y, center.z)
        when [:y, :min] then Geom::Point3d.new(center.x, @box_min.y, center.z)
        when [:y, :max] then Geom::Point3d.new(center.x, @box_max.y, center.z)
        when [:z, :min] then Geom::Point3d.new(center.x, center.y, @box_min.z)
        when [:z, :max] then Geom::Point3d.new(center.x, center.y, @box_max.z)
        else center
        end
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

      def offset_point(point, axis, distance)
        point.offset(axis, distance)
      end

      def gizmo_extension_length
        dims = box_dimensions
        largest = [dims.x.abs, dims.y.abs, dims.z.abs].max
        [[largest * 0.18, MIN_BOX_SIZE].max, 500.mm].min
      end

      def corners
        [
          Geom::Point3d.new(@box_min.x, @box_min.y, @box_min.z),
          Geom::Point3d.new(@box_max.x, @box_min.y, @box_min.z),
          Geom::Point3d.new(@box_max.x, @box_max.y, @box_min.z),
          Geom::Point3d.new(@box_min.x, @box_max.y, @box_min.z),
          Geom::Point3d.new(@box_min.x, @box_min.y, @box_max.z),
          Geom::Point3d.new(@box_max.x, @box_min.y, @box_max.z),
          Geom::Point3d.new(@box_max.x, @box_max.y, @box_max.z),
          Geom::Point3d.new(@box_min.x, @box_max.y, @box_max.z)
        ]
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
          offset_point(
            offset_point(center, axis_a, Math.cos(angle) * half_size),
            axis_b,
            Math.sin(angle) * half_size
          )
        end
      end

      def handle_color(handle_id)
        case handle_id
        when HANDLE_MOVE_X, HANDLE_RESIZE_MIN_X, HANDLE_RESIZE_MAX_X
          Sketchup::Color.new(220, 70, 55)
        when HANDLE_MOVE_Y, HANDLE_RESIZE_MIN_Y, HANDLE_RESIZE_MAX_Y
          Sketchup::Color.new(70, 190, 90)
        when HANDLE_MOVE_Z, HANDLE_RESIZE_MIN_Z, HANDLE_RESIZE_MAX_Z
          Sketchup::Color.new(70, 120, 230)
        when HANDLE_MOVE_PLANE_XY
          Sketchup::Color.new(232, 164, 52)
        when HANDLE_MOVE_PLANE_XZ
          Sketchup::Color.new(188, 96, 188)
        when HANDLE_MOVE_PLANE_YZ
          Sketchup::Color.new(74, 182, 196)
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
          GaussianPoints::SceneBoundsProxy.update_clip_box_bounds(@box_min, @box_max)
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
