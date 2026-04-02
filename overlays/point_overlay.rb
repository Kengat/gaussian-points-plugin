# overlays/point_overlay.rb

module GaussianPoints
  module Overlays

    class PointOverlay < Sketchup::Overlay
      attr_accessor :occlusion_enabled
      attr_accessor :point_size
      attr_accessor :visible_color
      attr_accessor :hidden_color
      attr_reader   :downsample_factor

      # Новый флаг: использовать ли real color (из E57)
      attr_accessor :use_e57_color

      def initialize
        super("gaussian_points.cloud_overlay", "Gaussian Points Overlay")

        @all_points   = []   # обычные точки (x,y,z)
        @points       = []
        @all_colored  = []   # [ [pt, r,g,b], ... ]
        @colored_pts  = []

        @point_style       = 2   # filled square
        @point_size        = 4
        @occlusion_enabled = false
        @use_e57_color     = false  # по умолчанию false (использовать user color)

        @visible_color = Sketchup::Color.new(80,80,80,180)  # серый
        @hidden_color  = Sketchup::Color.new(255,0,0,180)   # красный
        @downsample_factor = 1.0
      end

      def draw(view)
        # 1) Рисуем "обычные" точки (одноцветные)
        unless @points.empty?
          if @occlusion_enabled
            camera_eye = view.camera.eye
            @points.each do |pt|
              color = visible_from_camera?(camera_eye, pt) ? @visible_color : @hidden_color
              view.draw_points([pt], @point_size, @point_style, color)
            end
          else
            view.draw_points(@points, @point_size, @point_style, @visible_color)
          end
        end

        # 2) Рисуем "цветные" точки
        unless @colored_pts.empty?
          camera_eye = @occlusion_enabled ? view.camera.eye : nil
          @colored_pts.each do |(pt, rr, gg, bb)|
            # Если use_e57_color == true => используем (rr,gg,bb)
            # Иначе используем user color (visible_color / hidden_color)
            if @occlusion_enabled && camera_eye
              visible = visible_from_camera?(camera_eye, pt)
              if visible
                if @use_e57_color
                  c = Sketchup::Color.new(rr, gg, bb)
                else
                  c = @visible_color
                end
              else
                c = @hidden_color
              end
            else
              # нет occlusion
              if @use_e57_color
                c = Sketchup::Color.new(rr, gg, bb)
              else
                c = @visible_color
              end
            end

            view.draw_points([pt], @point_size, @point_style, c)
          end
        end
      end

      def getExtents
        bb = Geom::BoundingBox.new
        @points.each {|p| bb.add(p) }
        @colored_pts.each {|(p,_,_,_)| bb.add(p) }
        bb
      end

      # ------------ Обычные точки -----------
      def add_points(new_points)
        @all_points.concat(new_points)
        @all_points.uniq!
        apply_downsample(@downsample_factor)
      end

      def clear_points
        @all_points.clear
        @points.clear
        @all_colored.clear
        @colored_pts.clear
        Sketchup.active_model.active_view.invalidate
      end

      def all_points
        # объединяем
        arr = @points + @colored_pts.map{|(p,_,_,_)| p}
        arr
      end

      def apply_downsample(factor)
        @downsample_factor = factor.clamp(0.0, 1.0)
        if @all_points.empty?
          @points = []
        else
          need = (@all_points.size * @downsample_factor).round
          @points = @all_points.shuffle.first(need)
        end
        apply_downsample_colored(@downsample_factor)
        Sketchup.active_model.active_view.invalidate
      end

      # ------------ Цветные точки -----------
      def add_colored_points(data)
        # data: [ [pt, r,g,b], ... ]
        @all_colored.concat(data)
        apply_downsample_colored(@downsample_factor)
      end

      def apply_downsample_colored(factor)
        return if @all_colored.empty?
        needed = (@all_colored.size * factor).round
        @colored_pts = @all_colored.shuffle.first(needed)
      end

      private

      def visible_from_camera?(eye, pt)
        dir = pt - eye
        return true if dir.length < 0.001
        res = Sketchup.active_model.raytest([eye, dir])
        return true if res.nil?
        ipt, _ = res
        dist_eye_int = eye.distance(ipt)
        dist_eye_pt  = eye.distance(pt)
        (dist_eye_int >= dist_eye_pt - 0.001.mm)
      end

    end

  end
end
