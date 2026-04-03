module GaussianPoints
  module UIparts
    class MoveTool < OrientedBoxGizmoTool
      def initialize
        super(MoveToolManager)
      end

      def activate(view = nil)
        MoveToolManager.activate
        super(view)
      end

      def deactivate(view = nil)
        super(view)
        MoveToolManager.deactivate
      end

      def onMouseMove(flags, x, y, view)
        @last_mouse_x = x
        @last_mouse_y = y

        if @drag
          apply_drag(x, y, view)
          return
        end

        handle_id = MoveToolManager.selected? ? pick_handle(x, y, view, flags) : MoveToolManager::HANDLE_NONE
        if handle_id != MoveToolManager::HANDLE_NONE
          MoveToolManager.set_hovered_item(nil)
          MoveToolManager.set_hovered_handle(handle_id)
        else
          MoveToolManager.set_hovered_handle(MoveToolManager::HANDLE_NONE)
          MoveToolManager.set_hovered_item(MoveToolManager.pick_item(x, y, view))
        end
      end

      def onLButtonDown(flags, x, y, view)
        if MoveToolManager.selected?
          handle_id = pick_handle(x, y, view, flags)
          if handle_id != MoveToolManager::HANDLE_NONE
            start_drag(handle_id, x, y, view, flags)
            return
          end
        end

        item_id = MoveToolManager.pick_item(x, y, view)
        if item_id
          MoveToolManager.select_item(item_id)
        else
          MoveToolManager.clear_selection
        end
      end

      def onCancel(reason, view = nil)
        super(reason, view)
        return unless reason.to_i != 0

        MoveToolManager.clear_selection
      end

      def draw(view)
        MoveToolManager.draw(view)
      end
    end
  end
end
