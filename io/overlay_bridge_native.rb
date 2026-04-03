require 'fiddle'

module GaussianPoints
  module OverlayBridgeNative
    extend self

    def available?
      !GaussianPoints::NativePaths.bridge_dll.nil?
    end

    def clip_visual_available?
      setup_dll && install_hooks
    end

    def move_tool_visual_available?
      setup_dll && install_hooks && !@set_move_tool_box_state.nil?
    end

    def install_hooks
      return false unless setup_dll

      @install_all_hooks.call
      true
    rescue StandardError => e
      puts "[OverlayBridgeNative] hook install failed: #{e.message}"
      false
    end

    def sync_clip_box(enabled:, visible:, gizmo_visible:, center_scale_mode:, center_point:, half_extents:, axes:, hovered_handle:, active_handle:)
      return false unless setup_dll
      return false unless install_hooks

      call_box_state_sync(
        @set_clip_box_state,
        enabled: enabled,
        visible: visible,
        gizmo_visible: gizmo_visible,
        center_scale_mode: center_scale_mode,
        center_point: center_point,
        half_extents: half_extents,
        axes: axes,
        hovered_handle: hovered_handle,
        active_handle: active_handle
      )
      true
    rescue StandardError => e
      puts "[OverlayBridgeNative] clip sync failed: #{e.message}"
      false
    end

    def sync_move_tool_box(enabled:, visible:, gizmo_visible:, center_scale_mode:, center_point:, half_extents:, axes:, hovered_handle:, active_handle:)
      return false unless setup_dll
      return false unless install_hooks
      return false unless @set_move_tool_box_state

      call_box_state_sync(
        @set_move_tool_box_state,
        enabled: enabled,
        visible: visible,
        gizmo_visible: gizmo_visible,
        center_scale_mode: center_scale_mode,
        center_point: center_point,
        half_extents: half_extents,
        axes: axes,
        hovered_handle: hovered_handle,
        active_handle: active_handle
      )
      true
    rescue StandardError => e
      puts "[OverlayBridgeNative] move tool sync failed: #{e.message}"
      false
    end

    private

    def setup_dll
      return true if @dll_loaded

      bridge_path = GaussianPoints::NativePaths.bridge_dll
      return false unless bridge_path

      GaussianPoints::NativePaths.prepend_to_path(File.dirname(bridge_path))
      @dll = Fiddle.dlopen(bridge_path)
      @install_all_hooks = Fiddle::Function.new(@dll['InstallAllHooks'], [], Fiddle::TYPE_VOID)
      @set_clip_box_state = Fiddle::Function.new(
        @dll['SetClipBoxState'],
        [Fiddle::TYPE_INT, Fiddle::TYPE_INT, Fiddle::TYPE_INT, Fiddle::TYPE_INT, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT, Fiddle::TYPE_INT],
        Fiddle::TYPE_VOID
      )
      begin
        @set_move_tool_box_state = Fiddle::Function.new(
          @dll['SetMoveToolBoxState'],
          [Fiddle::TYPE_INT, Fiddle::TYPE_INT, Fiddle::TYPE_INT, Fiddle::TYPE_INT, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT, Fiddle::TYPE_INT],
          Fiddle::TYPE_VOID
        )
      rescue Fiddle::DLError
        @set_move_tool_box_state = nil
      end
      @dll_loaded = true
    rescue Fiddle::DLError => e
      puts "[OverlayBridgeNative] DLL load error: #{e.message}"
      @dll_loaded = false
      false
    end

    def pack_point(point)
      values =
        if point
          [point.x.to_f, point.y.to_f, point.z.to_f]
        else
          [0.0, 0.0, 0.0]
        end

      packed = values.pack('d3')
      memory = Fiddle::Pointer.malloc(packed.bytesize)
      memory[0, packed.bytesize] = packed
      memory
    end

    def pack_half_extents(half_extents)
      values =
        if half_extents
          [half_extents[:x].to_f, half_extents[:y].to_f, half_extents[:z].to_f]
        else
          [0.0, 0.0, 0.0]
        end

      pack_doubles(values)
    end

    def pack_axes(axes)
      values =
        if axes
          %i[x y z].flat_map do |axis|
            vector = axes[axis]
            if vector
              [vector.x.to_f, vector.y.to_f, vector.z.to_f]
            else
              [0.0, 0.0, 0.0]
            end
          end
        else
          [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        end

      pack_doubles(values)
    end

    def pack_doubles(values)
      packed = values.pack("d#{values.length}")
      memory = Fiddle::Pointer.malloc(packed.bytesize)
      memory[0, packed.bytesize] = packed
      memory
    end

    def call_box_state_sync(function, enabled:, visible:, gizmo_visible:, center_scale_mode:, center_point:, half_extents:, axes:, hovered_handle:, active_handle:)
      function.call(
        enabled ? 1 : 0,
        visible ? 1 : 0,
        gizmo_visible ? 1 : 0,
        center_scale_mode ? 1 : 0,
        pack_point(center_point),
        pack_half_extents(half_extents),
        pack_axes(axes),
        hovered_handle.to_i,
        active_handle.to_i
      )
    end
  end
end
