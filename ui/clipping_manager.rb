# ui/clipping_manager.rb

module GaussianPoints
  module UIparts

    module ClippingManager
      extend self

      @@active     = false
      @@clip_group = nil

      def active?
        @@active
      end

      def toggle_clip
        if @@active
          disable_clip
        else
          enable_clip
        end
      end

      def enable_clip
        return if @@active
        @@active = true

        # Создаём или показываем группу
        if @@clip_group && !@@clip_group.deleted?
          @@clip_group.visible = true
        else
          @@clip_group = create_clip_cube
        end

        puts "[ClipBox] Enabled."
        apply_clipping
      end

      def disable_clip
        return unless @@active
        @@active = false

        # Скрываем бокс
        if @@clip_group && !@@clip_group.deleted?
          @@clip_group.visible = false
        end

        # Возвращаем исходное состояние точек (factor = 1.0)
        overlay = GaussianPoints.overlay
        overlay.apply_downsample(1.0) if overlay

        puts "[ClipBox] Disabled."
      end

      def toggle_box_visibility
        return unless @@clip_group && !@@clip_group.deleted?
        @@clip_group.visible = !@@clip_group.visible?
      end

      # Применяет клиппинг по границам клип-бокса ко всем точкам
      def apply_clipping
        return unless @@active
        overlay = GaussianPoints.overlay
        return unless overlay
        return unless @@clip_group && !@@clip_group.deleted?

        box = bounding_box_of_group(@@clip_group)
        return unless box

        factor = overlay.downsample_factor

        # Обработка обычных точек (если они есть)
        allpts = overlay.send(:instance_variable_get, :@all_points)
        unless allpts.empty?
          inside = allpts.select { |pt| box.contains?(pt) }
          needed = (inside.size * factor).round
          newpts = inside.shuffle.first(needed)
          overlay.send(:instance_variable_set, :@points, newpts)
        end

        # Обработка цветных точек (если они есть)
        allcolored = overlay.send(:instance_variable_get, :@all_colored)
        unless allcolored.empty?
          inside_colored = allcolored.select { |arr| box.contains?(arr[0]) }
          needed_colored = (inside_colored.size * factor).round
          new_colored = inside_colored.shuffle.first(needed_colored)
          overlay.send(:instance_variable_set, :@colored_pts, new_colored)
        end

        Sketchup.active_model.active_view.invalidate

        puts "[ClipBox] apply_clipping => " +
             "uncolored: inside=#{allpts.size}, final=#{(allpts.empty? ? 0 : newpts.size)}; " +
             "colored: inside=#{allcolored.size}, final=#{(allcolored.empty? ? 0 : new_colored.size)}"
      end

      def reset_box_position
        return unless @@clip_group && !@@clip_group.deleted?
        @@clip_group.erase!
        @@clip_group = create_clip_cube
        apply_clipping
        puts "[ClipBox] reset_box_position => done"
      end

      private

      def create_clip_cube
        overlay = GaussianPoints.overlay
        grp = Sketchup.active_model.active_entities.add_group

        bb = bounding_box_of_cloud(overlay)
        edges = if bb.nil?
          create_edges_for_unit_cube
        else
          create_edges_for_bb(bb)
        end

        i = 0
        while i < edges.size
          grp.entities.add_line(edges[i], edges[i+1])
          i += 2
        end
        grp.name = "ClipBox"
        grp
      end

      # Возвращает габаритную рамку, объединяющую и обычные, и цветные точки
      def bounding_box_of_cloud(overlay)
        return nil unless overlay
        allpts = overlay.send(:instance_variable_get, :@all_points)
        allcolored = overlay.send(:instance_variable_get, :@all_colored)
        pts = allpts + allcolored.map { |arr| arr[0] }
        return nil if pts.empty?
        bb = Geom::BoundingBox.new
        bb.add(pts)
        bb
      end

      def create_edges_for_unit_cube
        s = 1000.mm
        a = Geom::Point3d.new(0, 0, 0)
        b = a.offset(X_AXIS, s)
        c = b.offset(Y_AXIS, s)
        d = a.offset(Y_AXIS, s)
        e = a.offset(Z_AXIS, s)
        f = b.offset(Z_AXIS, s)
        g = c.offset(Z_AXIS, s)
        h = d.offset(Z_AXIS, s)

        [
          a, b,  b, c,  c, d,  d, a,
          e, f,  f, g,  g, h,  h, e,
          a, e,  b, f,  c, g,  d, h
        ]
      end

      def create_edges_for_bb(bb)
        min = bb.min
        max = bb.max

        a = Geom::Point3d.new(min.x, min.y, min.z)
        b = Geom::Point3d.new(max.x, min.y, min.z)
        c = Geom::Point3d.new(max.x, max.y, min.z)
        d = Geom::Point3d.new(min.x, max.y, min.z)
        e = Geom::Point3d.new(min.x, min.y, max.z)
        f = Geom::Point3d.new(max.x, min.y, max.z)
        g = Geom::Point3d.new(max.x, max.y, max.z)
        h = Geom::Point3d.new(min.x, max.y, max.z)

        [
          a, b,  b, c,  c, d,  d, a,
          e, f,  f, g,  g, h,  h, e,
          a, e,  b, f,  c, g,  d, h
        ]
      end

      def bounding_box_of_group(grp)
        bb = Geom::BoundingBox.new
        grp.entities.grep(Sketchup::Edge).each do |edge|
          bb.add(edge.start.position.transform(grp.transformation))
          bb.add(edge.end.position.transform(grp.transformation))
        end
        bb
      end

    end

  end
end
