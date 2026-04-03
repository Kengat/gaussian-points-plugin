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

    def install_hooks
      return false unless setup_dll

      @install_all_hooks.call
      true
    rescue StandardError => e
      puts "[OverlayBridgeNative] hook install failed: #{e.message}"
      false
    end

    def sync_clip_box(enabled:, visible:, gizmo_visible:, min_point:, max_point:, hovered_handle:, active_handle:)
      return false unless setup_dll
      return false unless install_hooks

      min_ptr = pack_point(min_point)
      max_ptr = pack_point(max_point)
      @set_clip_box_state.call(
        enabled ? 1 : 0,
        visible ? 1 : 0,
        gizmo_visible ? 1 : 0,
        min_ptr,
        max_ptr,
        hovered_handle.to_i,
        active_handle.to_i
      )
      true
    rescue StandardError => e
      puts "[OverlayBridgeNative] clip sync failed: #{e.message}"
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
        [Fiddle::TYPE_INT, Fiddle::TYPE_INT, Fiddle::TYPE_INT, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT, Fiddle::TYPE_INT],
        Fiddle::TYPE_VOID
      )
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
  end
end
