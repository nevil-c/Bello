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
                    self.surfaces.append((widget,'label',None,(widget.winfo_width(),widget.winfo_height())))
            for child in children:collect(child)
        collect(root)

    def update(self,progress):
        boundary=self.root.winfo_rootx()+self.root.winfo_width()*progress
        for widget,kind,item,size in self.surfaces:
            if not widget.winfo_exists():continue
            width,height=size if size else (widget.winfo_width(),widget.winfo_height())
            extent=int(max(0,min(width,boundary-widget.winfo_rootx())))
            if kind=='frame':item.place_configure(width=extent)
            elif kind=='canvas':widget.coords(item,0,0,extent,height)
            else:
                photo=tk.PhotoImage(width=max(1,width),height=max(1,height))
                photo.put(self.base,to=(0,0,max(1,width),max(1,height)))
                if extent:photo.put(self.colour,to=(0,0,extent,max(1,height)))
                widget.configure(image=photo,compound='center',padx=0,pady=0)
                widget._sweep_image=photo
