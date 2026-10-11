"""Paint a shared screen-wide sweep behind native Tk controls."""
import tkinter as tk

class BackgroundSweep:
    def __init__(self,root,base,colour):
        self.root=root;self.base=base;self.colour=colour;self.surfaces=[]
        def collect(widget):
            children=widget.winfo_children()
            try:matches=widget.cget('bg')==base
            except tk.TclError:matches=False
            if matches:
                if isinstance(widget,(tk.Tk,tk.Frame)):
                    overlay=tk.Frame(widget,bg=colour)
                    overlay.place(x=0,y=0,width=0,relheight=1);overlay.lower()
                    self.surfaces.append((widget,'frame',overlay,None))
                elif isinstance(widget,tk.Canvas):
                    rect=widget.create_rectangle(0,0,0,0,fill=colour,outline='')
                    widget.tag_lower(rect)
                    self.surfaces.append((widget,'canvas',rect,None))
                elif isinstance(widget,tk.Label):
                    # [bitmap, extent already painted, size the bitmap was cut for(, options to restore)]
                    self.surfaces.append((widget,'label',[None,0,None],None))
            for child in children:collect(child)
        collect(root)

    def clear(self):
        """Remove every overlay, rectangle and label bitmap. Dropping the object is not enough:
        the paint stays on screen, and screens that are refreshed in place keep showing it."""
        for widget,kind,item,size in self.surfaces:
            if not widget.winfo_exists():continue
            if kind=='frame':item.destroy()
            elif kind=='canvas':widget.delete(item)
            elif item[0] is not None:
                widget.configure(image='',**item[3])
                widget._sweep_image=None
        self.surfaces=[]

    def update(self,progress):
        boundary=self.root.winfo_rootx()+self.root.winfo_width()*progress
        for widget,kind,item,size in self.surfaces:
            if not widget.winfo_exists():continue
            width,height=size if size else (widget.winfo_width(),widget.winfo_height())
            extent=int(max(0,min(width,boundary-widget.winfo_rootx())))
            if kind=='frame':item.place_configure(width=extent)
            elif kind=='canvas':widget.coords(item,0,0,extent,height)
            else:
                # Cut one bitmap per label and repaint one strip per frame. Rebuilding the whole
                # face every frame is what the Pi's software X server cannot keep up with.
                # bd=0: a label's border is added to the image when sizing it, so the label would
                # grow 2px per side, the bitmap would be re-cut bigger, and it would never settle.
                if item[0] is None or item[2]!=(width,height):
                    if len(item)==3:item.append({o:widget.cget(o) for o in ('compound','padx','pady','bd')})
                    item[0]=tk.PhotoImage(width=max(1,width),height=max(1,height))
                    item[2]=(width,height);item[1]=0
                    widget.configure(image=item[0],compound='center',padx=0,pady=0,bd=0)
                    widget._sweep_image=item[0]
                if extent!=item[1]:
                    item[0].put(self.colour if extent>item[1] else self.base,
                                to=(min(extent,item[1]),0,max(extent,item[1]),max(1,height)))
                    item[1]=extent
