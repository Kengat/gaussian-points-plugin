from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from tkinter import ttk

from . import APP_VERSION, paths, store
from .native_preview import NativeSplatPreview, preview_runtime_available, preview_runtime_error
from .pipeline import copy_input_images, ensure_project_camera_manifests, list_project_images
from .ply import PreviewPoint, read_preview_points


class PreviewCanvas(tk.Canvas):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg="#f3eee6", highlightthickness=0)
        self.points: list[PreviewPoint] = []
        self.angle_x = -0.35
        self.angle_y = 0.75
        self.zoom = 220.0
        self.last_mouse: tuple[int, int] | None = None
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_points(self, points: list[PreviewPoint]) -> None:
        self.points = points
        self.redraw()

    def _on_press(self, event: tk.Event) -> None:
        self.last_mouse = (event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:
        if not self.last_mouse:
            return
        dx = event.x - self.last_mouse[0]
        dy = event.y - self.last_mouse[1]
        self.last_mouse = (event.x, event.y)
        self.angle_y += dx * 0.01
        self.angle_x += dy * 0.01
        self.redraw()

    def _on_wheel(self, event: tk.Event) -> None:
        self.zoom = max(40.0, min(640.0, self.zoom + (event.delta / 120.0) * 18.0))
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        if not self.points:
            self.create_text(
                self.winfo_width() / 2,
                self.winfo_height() / 2,
                text="Run a job to preview the generated splat.",
                fill="#6c6258",
                font=("Georgia", 12),
            )
            return

        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        cos_x, sin_x = math.cos(self.angle_x), math.sin(self.angle_x)
        cos_y, sin_y = math.cos(self.angle_y), math.sin(self.angle_y)
        rendered = []
        for point in self.points:
            x, y, z = point.x, point.y, point.z
            xz_x = (x * cos_y) + (z * sin_y)
            xz_z = (-x * sin_y) + (z * cos_y)
            yz_y = (y * cos_x) - (xz_z * sin_x)
            yz_z = (y * sin_x) + (xz_z * cos_x)
            depth = yz_z + 3.5
            if depth <= 0.1:
                continue
            scale = self.zoom / depth
            screen_x = (width * 0.5) + (xz_x * scale)
            screen_y = (height * 0.5) - (yz_y * scale)
            radius = max(1.0, min(8.0, point.scale * 0.08 * scale))
            rendered.append((depth, screen_x, screen_y, radius, point))

        for _depth, screen_x, screen_y, radius, point in sorted(rendered, key=lambda item: item[0], reverse=True):
            color = "#%02x%02x%02x" % (
                int(point.r * 255),
                int(point.g * 255),
                int(point.b * 255),
            )
            self.create_oval(
                screen_x - radius,
                screen_y - radius,
                screen_x + radius,
                screen_y + radius,
                fill=color,
                outline="",
            )


class CompanionApp:
    POLL_MS = 1200

    def __init__(self, root: tk.Tk, plugin_root: str | None = None) -> None:
        self.root = root
        self.plugin_root = plugin_root
        self.selected_project_id: str | None = None
        self.preview_path: str | None = None
        self.preview_stamp: int | None = None
        self.preview_mode = "native" if preview_runtime_available() else "legacy"
        self.preview_runtime_message = preview_runtime_error()

        paths.ensure_runtime_dirs()
        store.init_db()
        self._configure_root()
        self._build_ui()
        self.refresh_projects()
        self.root.after(self.POLL_MS, self._poll)

    def _sample_dataset_dir(self) -> Path | None:
        root = Path(__file__).resolve().parents[1]
        sample_dir = root / "sample_datasets" / "nerf_synthetic_lego_12" / "images"
        return sample_dir if sample_dir.exists() else None

    def _configure_root(self) -> None:
        self.root.title("Gaussian Points Companion")
        self.root.geometry("1360x860")
        self.root.minsize(1180, 760)
        self.root.configure(bg="#ece3d4")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("App.TFrame", background="#ece3d4")
        style.configure("Panel.TFrame", background="#fbf7f1")
        style.configure("Panel.TLabel", background="#fbf7f1", foreground="#241d18")
        style.configure("Hero.TLabel", background="#fbf7f1", foreground="#241d18", font=("Georgia", 24, "bold"))
        style.configure("Sub.TLabel", background="#fbf7f1", foreground="#6f6458", font=("Segoe UI", 10))
        style.configure("Body.TLabel", background="#fbf7f1", foreground="#3d332b", font=("Segoe UI", 10))
        style.configure("Accent.TButton", background="#b75d39", foreground="#ffffff", borderwidth=0, padding=10)
        style.map("Accent.TButton", background=[("active", "#9f4d2b")])
        style.configure("Quiet.TButton", background="#efe6d9", foreground="#2f251f", borderwidth=0, padding=10)
        style.configure("Treeview", background="#fffdf9", fieldbackground="#fffdf9", rowheight=28)
        style.configure("Treeview.Heading", background="#efe4d5", foreground="#30261f")

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, style="App.TFrame", padding=14)
        shell.pack(fill="both", expand=True)

        sidebar = ttk.Frame(shell, style="Panel.TFrame", padding=12, width=270)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ttk.Label(sidebar, text="Projects", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(
            sidebar,
            text="Pick a project or create one from photos.",
            style="Sub.TLabel",
            wraplength=220,
        ).pack(anchor="w", pady=(4, 10))

        self.project_tree = ttk.Treeview(sidebar, columns=("status",), show="tree headings", height=24)
        self.project_tree.heading("#0", text="Project")
        self.project_tree.heading("status", text="Status")
        self.project_tree.column("#0", width=165)
        self.project_tree.column("status", width=70, anchor="center")
        self.project_tree.pack(fill="both", expand=True)
        self.project_tree.bind("<<TreeviewSelect>>", lambda _event: self._on_project_selected())

        ttk.Button(sidebar, text="Reveal Data Folder", style="Quiet.TButton", command=self.open_data_folder).pack(fill="x", pady=(10, 0))

        content = ttk.Frame(shell, style="App.TFrame")
        content.pack(side="left", fill="both", expand=True, padx=(14, 0))

        hero = ttk.Frame(content, style="App.TFrame")
        hero.pack(fill="x")
        ttk.Label(hero, text="Gaussian Splat Studio", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(
            hero,
            text="Create a project, add photos, run the pipeline, inspect the result, and export it back to SketchUp.",
            style="Body.TLabel",
            wraplength=920,
        ).pack(anchor="w", pady=(6, 0))

        toolbar = ttk.Frame(content, style="Panel.TFrame", padding=12)
        toolbar.pack(fill="x", pady=(12, 0))
        ttk.Button(toolbar, text="New Project From Photos", style="Accent.TButton", command=self.create_project).pack(side="left")
        ttk.Button(toolbar, text="Add Photos", style="Quiet.TButton", command=self.add_photos_to_project).pack(side="left", padx=(8, 0))
        self.sample_button = ttk.Button(toolbar, text="Create Sample Project", style="Quiet.TButton", command=self.create_sample_project)
        self.sample_button.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Start", style="Quiet.TButton", command=self.start_job).pack(side="left", padx=(18, 0))
        ttk.Button(toolbar, text="Restart", style="Quiet.TButton", command=self.restart_job).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Stop", style="Quiet.TButton", command=self.stop_job).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Open Export Folder", style="Quiet.TButton", command=self.open_latest_export).pack(side="left", padx=(18, 0))
        if not self._sample_dataset_dir():
            self.sample_button.state(["disabled"])

        controls = ttk.Frame(content, style="App.TFrame")
        controls.pack(fill="both", expand=True, pady=(12, 0))

        preview_column = ttk.Frame(controls, style="App.TFrame")
        preview_column.pack(side="left", fill="both", expand=True)

        self.project_title = ttk.Label(preview_column, text="No project selected", style="Hero.TLabel")
        self.project_title.pack(anchor="w")
        self.project_meta = ttk.Label(preview_column, text="Create a project from photos or use the sample project.", style="Sub.TLabel")
        self.project_meta.pack(anchor="w", pady=(4, 10))

        preview_panel = ttk.Frame(preview_column, style="Panel.TFrame", padding=10)
        preview_panel.pack(fill="both", expand=True)

        preview_head = ttk.Frame(preview_panel, style="Panel.TFrame")
        preview_head.pack(fill="x", pady=(0, 8))
        preview_title = "Scene Preview (Native Gaussian)" if self.preview_mode == "native" else "Scene Preview (Lightweight)"
        ttk.Label(preview_head, text=preview_title, style="Body.TLabel", font=("Segoe UI", 11, "bold")).pack(side="left")
        preview_hint_text = (
            "Same gaussian renderer path as SketchUp. LMB orbit, Shift+LMB or RMB/MMB pan, wheel zoom."
            if self.preview_mode == "native"
            else "Quick orbit preview only. Final splats use the full renderer in SketchUp."
        )
        self.preview_hint = ttk.Label(preview_head, text=preview_hint_text, style="Sub.TLabel")
        self.preview_hint.pack(side="right")

        preview_host = tk.Frame(preview_panel, bg="#f3eee6", highlightthickness=0)
        preview_host.pack(fill="both", expand=True)

        if self.preview_mode == "native":
            self.preview_view = NativeSplatPreview(preview_host, bg="#f3eee6", highlightthickness=0)
        else:
            self.preview_view = PreviewCanvas(preview_host)
        self.preview_view.pack(fill="both", expand=True)

        self.empty_state = tk.Frame(preview_host, bg="#f3eee6")
        self.empty_state.place(relx=0.5, rely=0.5, anchor="center")
        self.empty_title = tk.Label(
            self.empty_state,
            text="Start with photos or a sample scene",
            bg="#f3eee6",
            fg="#241d18",
            font=("Georgia", 20, "bold"),
        )
        self.empty_title.pack()
        self.empty_text = tk.Label(
            self.empty_state,
            text="Create a project, add images, then press Start.",
            bg="#f3eee6",
            fg="#6f6458",
            font=("Segoe UI", 11),
        )
        self.empty_text.pack(pady=(8, 14))
        empty_buttons = tk.Frame(self.empty_state, bg="#f3eee6")
        empty_buttons.pack()
        tk.Button(
            empty_buttons,
            text="New Project From Photos",
            command=self.create_project,
            bg="#b75d39",
            fg="white",
            activebackground="#9f4d2b",
            activeforeground="white",
            relief="flat",
            padx=14,
            pady=8,
        ).pack(side="left")
        self.empty_sample_button = tk.Button(
            empty_buttons,
            text="Create Sample Project",
            command=self.create_sample_project,
            bg="#e6d9c8",
            fg="#2f251f",
            activebackground="#d7c5af",
            relief="flat",
            padx=14,
            pady=8,
        )
        self.empty_sample_button.pack(side="left", padx=(10, 0))
        if not self._sample_dataset_dir():
            self.empty_sample_button.configure(state="disabled")

        default_footer = "No result yet."
        if self.preview_mode == "native":
            default_footer = "Native gaussian preview ready. Double-click or press F to fit, R to reset the view."
        elif self.preview_runtime_message:
            default_footer = f"Fell back to lightweight preview: {self.preview_runtime_message}"
        self.preview_footer = ttk.Label(preview_panel, text=default_footer, style="Sub.TLabel", wraplength=920)
        self.preview_footer.pack(anchor="w", pady=(8, 0))

        inspector = ttk.Frame(controls, style="App.TFrame", width=360)
        inspector.pack(side="left", fill="y", padx=(14, 0))
        inspector.pack_propagate(False)

        self.status_card = self._make_card(inspector, "Job Status", "No active job.")
        self.status_card.pack(fill="x")
        self.export_card = self._make_card(inspector, "SketchUp Handoff", "No export yet.")
        self.export_card.pack(fill="x", pady=(10, 0))

        notebook = ttk.Notebook(inspector)
        notebook.pack(fill="both", expand=True, pady=(10, 0))

        logs_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=10)
        notebook.add(logs_tab, text="Logs")
        self.logs_text = tk.Text(logs_tab, wrap="word", bg="#fffdf8", fg="#2b241f", relief="flat", font=("Consolas", 10))
        self.logs_text.pack(fill="both", expand=True)
        self.logs_text.configure(state="disabled")

        photos_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=10)
        notebook.add(photos_tab, text="Photos")
        self.photos_list = tk.Listbox(photos_tab, bg="#fffdf8", fg="#2c241f", relief="flat", font=("Segoe UI", 10))
        self.photos_list.pack(fill="both", expand=True)

    def _make_card(self, master: tk.Misc, title: str, body: str) -> ttk.Frame:
        card = ttk.Frame(master, style="Panel.TFrame", padding=14)
        ttk.Label(card, text=title, style="Body.TLabel", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        body_label = ttk.Label(card, text=body, style="Sub.TLabel", wraplength=280, justify="left")
        body_label.pack(anchor="w", pady=(8, 0))
        card.body_label = body_label
        return card

    def _poll(self) -> None:
        try:
            self.refresh_projects(selected_only=True)
        finally:
            self.root.after(self.POLL_MS, self._poll)

    def refresh_projects(self, selected_only: bool = False) -> None:
        projects = store.list_projects()
        current_ids = set(self.project_tree.get_children())
        desired_ids = {project["id"] for project in projects}
        if not selected_only or current_ids != desired_ids:
            for item_id in current_ids - desired_ids:
                self.project_tree.delete(item_id)
            for project in projects:
                values = (project["status"],)
                if project["id"] in current_ids:
                    self.project_tree.item(project["id"], text=project["name"], values=values)
                else:
                    self.project_tree.insert("", "end", iid=project["id"], text=project["name"], values=values)

        if self.selected_project_id and self.selected_project_id not in desired_ids:
            self.selected_project_id = None

        if not self.selected_project_id and projects:
            self.selected_project_id = projects[0]["id"]
            self.project_tree.selection_set(self.selected_project_id)
        self._refresh_detail()

    def _on_project_selected(self) -> None:
        selection = self.project_tree.selection()
        self.selected_project_id = selection[0] if selection else None
        self._refresh_detail()

    def _set_empty_state(self, title: str, message: str, visible: bool) -> None:
        self.empty_title.configure(text=title)
        self.empty_text.configure(text=message)
        if visible:
            self.preview_view.pack_forget()
            self.empty_state.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.empty_state.place_forget()
            if not self.preview_view.winfo_manager():
                self.preview_view.pack(fill="both", expand=True)

    def _clear_preview(self) -> None:
        if self.preview_mode == "native":
            self.preview_view.clear_scene()
        else:
            self.preview_view.set_points([])

    def _load_preview_scene(self, preview_path: str) -> None:
        if self.preview_mode == "native":
            self.preview_view.load_scene(preview_path, force_reload=True)
        else:
            points, _stats = read_preview_points(preview_path)
            self.preview_view.set_points(points)

    def _refresh_detail(self) -> None:
        if not self.selected_project_id:
            self.project_title.configure(text="No project selected")
            self.project_meta.configure(text="Create a project from photos or use the sample project.")
            self.status_card.body_label.configure(text="No job yet.")
            self.export_card.body_label.configure(text="No export yet.")
            self.photos_list.delete(0, tk.END)
            self.logs_text.configure(state="normal")
            self.logs_text.delete("1.0", tk.END)
            self.logs_text.configure(state="disabled")
            self._clear_preview()
            self.preview_path = None
            self.preview_stamp = None
            self.preview_footer.configure(text="The preview will appear here after a run.")
            self._set_empty_state(
                "Start with photos or a sample scene",
                "Use the buttons above. The sample project is the fastest way to test the pipeline.",
                True,
            )
            return

        project = store.get_project(self.selected_project_id)
        if not project:
            return

        jobs = store.list_jobs(project["id"])
        latest_job = jobs[0] if jobs else None
        images = list_project_images(project["id"])
        manifest_status = ensure_project_camera_manifests(project["id"])
        camera_text = "SfM-only cameras"
        if manifest_status["mode"] == "manifest":
            camera_text = f"camera manifest: {int(manifest_status['usable_views'])} views"
        self.project_title.configure(text=project["name"])
        self.project_meta.configure(text=f"{len(images)} photos | {camera_text} | {project['status']} | {project['workspace_dir']}")

        status_text = "No job yet. Press Start to run the pipeline."
        if latest_job:
            percent = int(float(latest_job["progress"]) * 100)
            status_text = f"{latest_job['status']} | {latest_job['stage']} | {percent}%\n{latest_job['message']}"
        self.status_card.body_label.configure(text=status_text)

        export_text = "No export yet."
        if project.get("last_manifest_path"):
            export_text = (
                f"Latest manifest:\n{project['last_manifest_path']}\n\n"
                "Use Open Export Folder here or Import Latest Companion Result in SketchUp."
            )
        self.export_card.body_label.configure(text=export_text)

        self.photos_list.delete(0, tk.END)
        for image in images:
            self.photos_list.insert(tk.END, image.name)

        self._refresh_logs(latest_job)
        self._refresh_preview(project, latest_job, len(images))

    def _refresh_logs(self, latest_job: dict | None) -> None:
        content = ""
        if latest_job and latest_job.get("log_path") and Path(latest_job["log_path"]).exists():
            content = Path(latest_job["log_path"]).read_text(encoding="utf-8")
        self.logs_text.configure(state="normal")
        self.logs_text.delete("1.0", tk.END)
        self.logs_text.insert("1.0", content)
        self.logs_text.see(tk.END)
        self.logs_text.configure(state="disabled")

    def _refresh_preview(self, project: dict, latest_job: dict | None, image_count: int) -> None:
        preview_path = project.get("last_result_ply")
        if not preview_path or not Path(preview_path).exists():
            if self.preview_path is not None:
                self.preview_path = None
                self.preview_stamp = None
                self._clear_preview()
            if image_count == 0:
                self.preview_footer.configure(text="This project has no photos yet.")
                self._set_empty_state(
                    "Add photos to this project",
                    "Use Add Photos or create a new project from images.",
                    True,
                )
            elif latest_job and latest_job.get("status") == "running":
                self.preview_footer.configure(text="Training is running. Logs update on the right.")
                self._set_empty_state(
                    "Training in progress",
                    "The preview will appear here automatically when the job finishes.",
                    True,
                )
            else:
                self.preview_footer.configure(text=f"{image_count} photos loaded. Press Start to build the scene.")
                self._set_empty_state(
                    "Ready to run",
                    "Photos are loaded. Press Start above to generate the scene.",
                    True,
                )
            return
        preview_stamp = Path(preview_path).stat().st_mtime_ns
        if preview_path == self.preview_path and preview_stamp == self.preview_stamp:
            self._set_empty_state("", "", False)
            return
        try:
            stats = None
            if self.preview_mode == "legacy":
                points, stats = read_preview_points(preview_path)
            else:
                _points, stats = read_preview_points(preview_path, sample_limit=64)
        except Exception as error:
            self._clear_preview()
            self.preview_footer.configure(text=f"Preview load failed: {error}")
            self._set_empty_state("Preview failed", str(error), True)
            return
        self.preview_path = preview_path
        self.preview_stamp = preview_stamp
        self._load_preview_scene(preview_path)
        if self.preview_mode == "native":
            self.preview_footer.configure(
                text=(
                    f"Rendering {stats['point_count']} splats with the native gaussian preview. "
                    f"Same renderer family as SketchUp, but with an interactive standalone camera. "
                    f"Bounds: {stats['bounds']['min']} -> {stats['bounds']['max']}"
                )
            )
        else:
            self.preview_footer.configure(
                text=(
                    f"Previewing {stats['point_count']} exported splats in lightweight mode. "
                    f"The saved PLY keeps full gaussian data for the SketchUp renderer. "
                    f"Bounds: {stats['bounds']['min']} -> {stats['bounds']['max']}"
                )
            )
        self._set_empty_state("", "", False)

    def _launch_worker(self, job_id: str) -> None:
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW
        worker_python = paths.preferred_worker_python()
        python_executable = str(worker_python or Path(sys.executable))
        repo_root = str(paths.repo_root())
        env = os.environ.copy()
        pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = repo_root if not pythonpath else f"{repo_root}{os.pathsep}{pythonpath}"
        subprocess.Popen(
            [python_executable, "-m", "companion_app.worker_entry", job_id],
            cwd=repo_root,
            env=env,
            creationflags=creationflags,
        )

    def _create_project_from_paths(self, name: str, image_paths: list[str], note: str | None = None) -> None:
        clean_name = name.strip()
        if not clean_name:
            messagebox.showinfo("Project Name Required", "Enter a project name first.", parent=self.root)
            return
        if not image_paths:
            messagebox.showinfo("No Photos Selected", "Choose one or more photos to create the project.", parent=self.root)
            return
        project = store.create_project(name=clean_name, note=note)
        copy_input_images(project["id"], image_paths)
        self.selected_project_id = project["id"]
        self.refresh_projects()
        self.project_tree.selection_set(project["id"])

    def create_project(self) -> None:
        name = simpledialog.askstring(
            "Project Name",
            "Project name:\n\nExample: Red Vase Capture",
            parent=self.root,
        )
        if name is None:
            return
        image_paths = filedialog.askopenfilenames(
            title="Choose project photos",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp")],
            parent=self.root,
        )
        self._create_project_from_paths(name, list(image_paths))

    def add_photos_to_project(self) -> None:
        if not self.selected_project_id:
            messagebox.showinfo("No Project Selected", "Create or select a project first.", parent=self.root)
            return
        image_paths = filedialog.askopenfilenames(
            title="Add photos to project",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp")],
            parent=self.root,
        )
        if not image_paths:
            return
        copy_input_images(self.selected_project_id, list(image_paths))
        self.refresh_projects()

    def create_sample_project(self) -> None:
        sample_dir = self._sample_dataset_dir()
        if not sample_dir:
            messagebox.showerror("Sample Missing", "Bundled sample dataset was not found.", parent=self.root)
            return
        image_paths = [str(path) for path in sorted(sample_dir.glob("*.png"))]
        self._create_project_from_paths("Sample Lego 12 Views", image_paths, note="bundled_sample:nerf_synthetic_lego_12")

    def _prompt_job_settings(self, force_restart: bool) -> dict | None:
        settings = store.default_job_settings(force_restart=force_restart)
        steps = simpledialog.askinteger(
            "Training Steps",
            "How many training steps should this run use?",
            parent=self.root,
            minvalue=200,
            maxvalue=20000,
            initialvalue=int(settings["train_steps"]),
        )
        if steps is None:
            return None
        settings["train_steps"] = int(steps)
        settings["densify_stop_iter"] = min(int(settings["densify_stop_iter"]), max(steps - 1, 1))
        settings["refine_scale2d_stop_iter"] = int(steps)
        return settings

    def _start_project_job(self, force_restart: bool) -> None:
        if not self.selected_project_id:
            messagebox.showinfo(
                "No Project Selected",
                "Create a project from photos or use Create Sample Project first.",
                parent=self.root,
            )
            return
        images = list_project_images(self.selected_project_id)
        if not images:
            messagebox.showinfo("No Photos", "Add photos to the selected project first.", parent=self.root)
            return
        manifest_status = ensure_project_camera_manifests(self.selected_project_id)
        existing = store.latest_job(self.selected_project_id)
        if existing and existing["status"] == "running":
            messagebox.showinfo("Job Running", "This project already has a running job.", parent=self.root)
            return
        settings = self._prompt_job_settings(force_restart=force_restart)
        if settings is None:
            return
        job = store.create_job(self.selected_project_id, settings)
        if manifest_status["mode"] == "manifest":
            repaired_count = int(manifest_status["repaired_manifests"])
            view_count = int(manifest_status["usable_views"])
            detail = f"Using camera manifest with {view_count} views."
            if repaired_count > 0:
                detail = f"Repaired project camera manifest and using {view_count} views."
            messagebox.showinfo("Camera Setup", detail, parent=self.root)
        else:
            messagebox.showwarning(
                "No Camera Manifest",
                "This project does not have a usable transforms.json camera manifest, so training will fall back to SfM-only cameras. Results may be much worse.",
                parent=self.root,
            )
        self._launch_worker(job["id"])
        self.refresh_projects()

    def start_job(self) -> None:
        self._start_project_job(force_restart=False)

    def continue_job(self) -> None:
        self._start_project_job(force_restart=False)

    def restart_job(self) -> None:
        self._start_project_job(force_restart=True)

    def stop_job(self) -> None:
        if not self.selected_project_id:
            messagebox.showinfo("No Project Selected", "Select a project first.", parent=self.root)
            return
        latest_job = store.latest_job(self.selected_project_id)
        if not latest_job:
            messagebox.showinfo("No Job", "This project does not have a job to stop yet.", parent=self.root)
            return
        store.request_job_stop(latest_job["id"])
        self.refresh_projects()

    def open_latest_export(self) -> None:
        if not self.selected_project_id:
            messagebox.showinfo("No Project Selected", "Select a project first.", parent=self.root)
            return
        project = store.get_project(self.selected_project_id)
        if not project or not project.get("last_manifest_path"):
            messagebox.showinfo("No Export Yet", "Run a project to generate an export first.", parent=self.root)
            return
        os.startfile(Path(project["last_manifest_path"]).parent)  # type: ignore[attr-defined]

    def open_data_folder(self) -> None:
        os.startfile(paths.data_root())  # type: ignore[attr-defined]


def launch(plugin_root: str | None = None) -> int:
    root = tk.Tk()
    CompanionApp(root, plugin_root=plugin_root)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", default=None)
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(APP_VERSION)
        return 0
    return launch(plugin_root=args.plugin_root)
