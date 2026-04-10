require 'json'

module GaussianPoints
  module UIparts
    module RenderItemRegistry
      extend self

      MIN_HALF_EXTENT = 1.mm
      HIGHLIGHT_NONE = 0
      HIGHLIGHT_HOVER = 1
      HIGHLIGHT_SELECTED = 2
      MODEL_ATTR_DICT = 'GaussianPoints'.freeze
      MODEL_ATTR_KEY = 'render_item_states_v1'.freeze

      def items
        @items ||= {}
      end

      def all_items
        items.values
      end

      def ordered_items
        all_items.sort_by { |item| item[:created_at] || 0 }
      end

      def find(item_id)
        item_id ? items[item_id] : nil
      end

      def pointcloud_items
        ordered_items.select { |item| item[:type] == :pointcloud }
      end

      def gaussian_items
        ordered_items.select { |item| item[:type] == :gaussian }
      end

      def register_pointcloud(name:, packed_points:, initial_state:)
        id = next_id('pc')
        GaussianPoints::Hook.clear_pointcloud_objects if pointcloud_items.empty?
        return nil unless GaussianPoints::Hook.upsert_pointcloud_object(id, packed_points)
        return nil unless GaussianPoints::Hook.set_pointcloud_object_transform(id, initial_state)

        items[id] = {
          id: id,
          type: :pointcloud,
          name: name,
          box_state: duplicate_snapshot(initial_state),
          base_box_state: duplicate_snapshot(initial_state),
          created_at: monotonic_stamp
        }

        sync_scene_bounds_proxy!
        persist_to_model!
        items[id]
      end

      def register_pointcloud_project(name:, project_path:, source_path: nil)
        id = next_id('pc')
        GaussianPoints::Hook.clear_pointcloud_objects if pointcloud_items.empty?
        initial_state = GaussianPoints::Hook.load_pointcloud_object_from_gasp(id, project_path)
        return nil unless initial_state

        items[id] = {
          id: id,
          type: :pointcloud,
          name: name,
          source_path: source_path,
          project_path: project_path,
          box_state: duplicate_snapshot(initial_state),
          base_box_state: duplicate_snapshot(initial_state),
          created_at: monotonic_stamp
        }

        sync_scene_bounds_proxy!
        persist_to_model!
        items[id]
      end

      def register_gaussian_file(name:, filename:)
        id = next_id('gs')
        GaussianPoints::GaussianSplats.clear_splat_objects if gaussian_items.empty?
        initial_state = GaussianPoints::GaussianSplats.load_gaussian_splats_file_as_object(id, filename)
        return nil unless initial_state

        items[id] = {
          id: id,
          type: :gaussian,
          name: name,
          source_path: filename,
          box_state: duplicate_snapshot(initial_state),
          base_box_state: duplicate_snapshot(initial_state),
          created_at: monotonic_stamp
        }

        sync_scene_bounds_proxy!
        persist_to_model!
        items[id]
      end

      def update_item_state(item_id, snapshot, persist: true, sync_proxy: true)
        item = find(item_id)
        return false unless item && snapshot

        item[:box_state] = duplicate_snapshot(snapshot)

        case item[:type]
        when :pointcloud
          GaussianPoints::Hook.set_pointcloud_object_transform(item_id, snapshot)
        when :gaussian
          GaussianPoints::GaussianSplats.set_object_transform(item_id, snapshot)
        end

        sync_scene_bounds_proxy! if sync_proxy
        persist_to_model! if persist
        true
      end

      def base_snapshot(item_id)
        item = find(item_id)
        item ? duplicate_snapshot(item[:base_box_state]) : nil
      end

      def reset_item_state(item_id, persist: true, sync_proxy: true)
        item = find(item_id)
        return false unless item && item[:base_box_state]

        update_item_state(item_id, item[:base_box_state], persist: persist, sync_proxy: sync_proxy)
      end

      def remove_item(item_id)
        item = items.delete(item_id)
        return false unless item

        case item[:type]
        when :pointcloud
          GaussianPoints::Hook.remove_pointcloud_object(item_id)
        when :gaussian
          GaussianPoints::GaussianSplats.remove_object(item_id)
        end

        sync_scene_bounds_proxy!
        persist_to_model!
        true
      end

      def clear_all
        apply_highlights(hovered_id: nil, selected_id: nil)
        items.clear
        GaussianPoints::Hook.clear_pointcloud_objects if defined?(GaussianPoints::Hook)
        GaussianPoints::GaussianSplats.clear_splat_objects if defined?(GaussianPoints::GaussianSplats)
        if defined?(GaussianPoints::SceneBoundsProxy)
          GaussianPoints::SceneBoundsProxy.clear_pointcloud
          GaussianPoints::SceneBoundsProxy.clear_splats
        end
        persist_to_model!
      end

      def persist_to_model!(model = Sketchup.active_model)
        return unless model

        payload = ordered_items.map do |item|
          {
            'id' => item[:id],
            'box_state' => serialize_snapshot(item[:box_state]),
            'base_box_state' => serialize_snapshot(item[:base_box_state] || item[:box_state])
          }
        end
        model.set_attribute(MODEL_ATTR_DICT, MODEL_ATTR_KEY, JSON.generate(payload))
      rescue StandardError
        nil
      end

      def restore_from_model!(model = Sketchup.active_model)
        return unless model

        raw = model.get_attribute(MODEL_ATTR_DICT, MODEL_ATTR_KEY)
        parsed = raw.is_a?(String) && !raw.empty? ? JSON.parse(raw) : []
        parsed.each do |entry|
          item = find(entry['id'])
          next unless item

          item[:base_box_state] = deserialize_snapshot(entry['base_box_state']) if entry['base_box_state']
          snapshot = deserialize_snapshot(entry['box_state'])
          update_item_state(item[:id], snapshot, persist: false, sync_proxy: false) if snapshot
        end
        model.active_view.invalidate
      rescue StandardError
        nil
      end

      def ensure_model_observer!(model = Sketchup.active_model)
        return unless model
        return if @observed_model == model && @history_observer

        detach_model_observer!
        @history_observer ||= HistoryObserver.new
        model.add_observer(@history_observer)
        @observed_model = model
      rescue StandardError
        nil
      end

      def detach_model_observer!
        return unless @observed_model && @history_observer

        @observed_model.remove_observer(@history_observer)
      rescue StandardError
        nil
      ensure
        @observed_model = nil
      end

      def apply_highlights(hovered_id:, selected_id:)
        ordered_items.each do |item|
          highlight_mode =
            if item[:id] == selected_id
              HIGHLIGHT_SELECTED
            elsif item[:id] == hovered_id
              HIGHLIGHT_HOVER
            else
              HIGHLIGHT_NONE
            end

          apply_item_highlight(item, highlight_mode)
        end
      end

      def supports_native_highlight?(item)
        return false unless item

        case item[:type]
        when :pointcloud
          defined?(GaussianPoints::Hook) &&
            GaussianPoints::Hook.respond_to?(:supports_highlight_api?) &&
            GaussianPoints::Hook.supports_highlight_api?
        when :gaussian
          defined?(GaussianPoints::GaussianSplats) &&
            GaussianPoints::GaussianSplats.respond_to?(:supports_highlight_api?) &&
            GaussianPoints::GaussianSplats.supports_highlight_api?
        else
          false
        end
      end

      def world_corners(item_or_state)
        state = item_or_state[:box_state] ? item_or_state[:box_state] : item_or_state
        center = state[:center]
        axis_x = normalized_axis(state[:axes][:x], :x)
        axis_y = normalized_axis(state[:axes][:y], :y)
        axis_z = normalized_axis(state[:axes][:z], :z)
        half_x = [state[:half_extents][:x].to_f, MIN_HALF_EXTENT.to_f].max
        half_y = [state[:half_extents][:y].to_f, MIN_HALF_EXTENT.to_f].max
        half_z = [state[:half_extents][:z].to_f, MIN_HALF_EXTENT.to_f].max

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

      def duplicate_snapshot(snapshot)
        {
          center: snapshot[:center].clone,
          half_extents: snapshot[:half_extents].transform_values(&:to_f),
          axes: snapshot[:axes].transform_values(&:clone)
        }
      end

      class HistoryObserver < Sketchup::ModelObserver
        def onTransactionUndo(model)
          RenderItemRegistry.restore_from_model!(model)
        end

        def onTransactionRedo(model)
          RenderItemRegistry.restore_from_model!(model)
        end
      end

      private

      def serialize_snapshot(snapshot)
        return nil unless snapshot

        {
          'center' => [snapshot[:center].x.to_f, snapshot[:center].y.to_f, snapshot[:center].z.to_f],
          'half_extents' => {
            'x' => snapshot[:half_extents][:x].to_f,
            'y' => snapshot[:half_extents][:y].to_f,
            'z' => snapshot[:half_extents][:z].to_f
          },
          'axes' => {
            'x' => vector_to_array(snapshot[:axes][:x]),
            'y' => vector_to_array(snapshot[:axes][:y]),
            'z' => vector_to_array(snapshot[:axes][:z])
          }
        }
      end

      def deserialize_snapshot(snapshot_hash)
        return nil unless snapshot_hash.is_a?(Hash)

        center_xyz = snapshot_hash['center']
        half_extents = snapshot_hash['half_extents']
        axes = snapshot_hash['axes']
        return nil unless center_xyz && half_extents && axes

        {
          center: Geom::Point3d.new(center_xyz[0].to_f, center_xyz[1].to_f, center_xyz[2].to_f),
          half_extents: {
            x: half_extents['x'].to_f,
            y: half_extents['y'].to_f,
            z: half_extents['z'].to_f
          },
          axes: {
            x: array_to_vector(axes['x'], :x),
            y: array_to_vector(axes['y'], :y),
            z: array_to_vector(axes['z'], :z)
          }
        }
      end

      def vector_to_array(vector)
        [vector.x.to_f, vector.y.to_f, vector.z.to_f]
      end

      def array_to_vector(value, fallback_axis)
        fallback =
          case fallback_axis
          when :x then [1.0, 0.0, 0.0]
          when :y then [0.0, 1.0, 0.0]
          else [0.0, 0.0, 1.0]
          end
        xyz = value.is_a?(Array) && value.length >= 3 ? value : fallback
        Geom::Vector3d.new(xyz[0].to_f, xyz[1].to_f, xyz[2].to_f)
      end

      def monotonic_stamp
        @stamp = @stamp.to_i + 1
      end

      def next_id(prefix)
        @id_sequence = @id_sequence.to_i + 1
        "#{prefix}_#{@id_sequence}"
      end

      def sync_scene_bounds_proxy!
        return unless defined?(GaussianPoints::SceneBoundsProxy)

        sync_type_bounds!(pointcloud_items, :update_pointcloud_bounds, :clear_pointcloud)
        sync_type_bounds!(gaussian_items, :update_splats_bounds, :clear_splats)
      end

      def sync_type_bounds!(type_items, update_method, clear_method)
        if type_items.empty?
          GaussianPoints::SceneBoundsProxy.public_send(clear_method)
          return
        end

        bounds = combined_bounds(type_items)
        if bounds
          GaussianPoints::SceneBoundsProxy.public_send(update_method, bounds[:min], bounds[:max])
        else
          GaussianPoints::SceneBoundsProxy.public_send(clear_method)
        end
      end

      def combined_bounds(type_items)
        bb = Geom::BoundingBox.new
        type_items.each do |item|
          world_corners(item).each { |corner| bb.add(corner) }
        end
        return nil if bb.empty?

        {
          min: [bb.min.x, bb.min.y, bb.min.z],
          max: [bb.max.x, bb.max.y, bb.max.z]
        }
      end

      def apply_item_highlight(item, highlight_mode)
        return unless item

        case item[:type]
        when :pointcloud
          return unless defined?(GaussianPoints::Hook)
          return unless GaussianPoints::Hook.respond_to?(:set_pointcloud_object_highlight)

          GaussianPoints::Hook.set_pointcloud_object_highlight(item[:id], highlight_mode)
        when :gaussian
          return unless defined?(GaussianPoints::GaussianSplats)
          return unless GaussianPoints::GaussianSplats.respond_to?(:set_object_highlight)

          GaussianPoints::GaussianSplats.set_object_highlight(item[:id], highlight_mode)
        end
      end

      def normalized_axis(vector, fallback_axis)
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
        result = vector.clone
        result.normalize!
        result.length = length.abs
        result.reverse! if length < 0.0
        result
      end

      def oriented_corner(center, axis_x, axis_y, axis_z, dx, dy, dz)
        point = center.clone
        point = point.offset(scaled_vector(axis_x, dx))
        point = point.offset(scaled_vector(axis_y, dy))
        point.offset(scaled_vector(axis_z, dz))
      end
    end
  end
end
