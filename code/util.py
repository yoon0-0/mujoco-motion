import math,time,os
import numpy as np
import tkinter as tk
import shapely as sp # handle polygon
import mediapy as media
from shapely import Polygon,LineString,Point # handle polygons
from scipy.spatial.distance import cdist
import torch

def rot_mtx(deg):
    """
        2 x 2 rotation matrix
    """
    theta = np.radians(deg)
    c, s = np.cos(theta), np.sin(theta)
    R = np.array(((c, -s), (s, c)))
    return R

def pr2t(p,R):
    """ 
        Convert pose to transformation matrix 
    """
    p0 = p.ravel() # flatten
    T = np.block([
        [R, p0[:, np.newaxis]],
        [np.zeros(3), 1]
    ])
    return T

def t2pr(T):
    """
        T to p and R
    """   
    p = T[...,:3,3]
    R = T[...,:3,:3]
    return p,R

def t2p(T):
    """
        T to p 
    """   
    p = T[:3,3]
    return p

def t2r(T):
    """
        T to R
    """   
    R = T[:3,:3]
    return R    

def rpy2r(rpy_rad):
    """
        roll,pitch,yaw in radian to R
    """
    roll  = rpy_rad[0]
    pitch = rpy_rad[1]
    yaw   = rpy_rad[2]
    Cphi  = np.math.cos(roll)
    Sphi  = np.math.sin(roll)
    Cthe  = np.math.cos(pitch)
    Sthe  = np.math.sin(pitch)
    Cpsi  = np.math.cos(yaw)
    Spsi  = np.math.sin(yaw)
    R     = np.array([
        [Cpsi * Cthe, -Spsi * Cphi + Cpsi * Sthe * Sphi, Spsi * Sphi + Cpsi * Sthe * Cphi],
        [Spsi * Cthe, Cpsi * Cphi + Spsi * Sthe * Sphi, -Cpsi * Sphi + Spsi * Sthe * Cphi],
        [-Sthe, Cthe * Sphi, Cthe * Cphi]
    ])
    assert R.shape == (3, 3)
    return R

def r2rpy(R,unit='rad'):
    """
        Rotation matrix to roll,pitch,yaw in radian
    """
    roll  = math.atan2(R[2, 1], R[2, 2])
    pitch = math.atan2(-R[2, 0], (math.sqrt(R[2, 1] ** 2 + R[2, 2] ** 2)))
    yaw   = math.atan2(R[1, 0], R[0, 0])
    if unit == 'rad':
        out = np.array([roll, pitch, yaw])
    elif unit == 'deg':
        out = np.array([roll, pitch, yaw])*180/np.pi
    else:
        out = None
        raise Exception("[r2rpy] Unknown unit:[%s]"%(unit))
    return out    

def r2w(R):
    """
        R to \omega
    """
    el = np.array([
            [R[2,1] - R[1,2]],
            [R[0,2] - R[2,0]], 
            [R[1,0] - R[0,1]]
        ])
    norm_el = np.linalg.norm(el)
    if norm_el > 1e-10:
        w = np.arctan2(norm_el, np.trace(R)-1) / norm_el * el
    elif R[0,0] > 0 and R[1,1] > 0 and R[2,2] > 0:
        w = np.array([[0, 0, 0]]).T
    else:
        w = np.math.pi/2 * np.array([[R[0,0]+1], [R[1,1]+1], [R[2,2]+1]])
    return w.flatten()

def r2quat(R):
    """ 
        Convert Rotation Matrix to Quaternion.  See rotation.py for notes 
        (https://gist.github.com/machinaut/dab261b78ac19641e91c6490fb9faa96)
    """
    R = np.asarray(R, dtype=np.float64)
    Qxx, Qyx, Qzx = R[..., 0, 0], R[..., 0, 1], R[..., 0, 2]
    Qxy, Qyy, Qzy = R[..., 1, 0], R[..., 1, 1], R[..., 1, 2]
    Qxz, Qyz, Qzz = R[..., 2, 0], R[..., 2, 1], R[..., 2, 2]
    # Fill only lower half of symmetric matrix
    K = np.zeros(R.shape[:-2] + (4, 4), dtype=np.float64)
    K[..., 0, 0] = Qxx - Qyy - Qzz
    K[..., 1, 0] = Qyx + Qxy
    K[..., 1, 1] = Qyy - Qxx - Qzz
    K[..., 2, 0] = Qzx + Qxz
    K[..., 2, 1] = Qzy + Qyz
    K[..., 2, 2] = Qzz - Qxx - Qyy
    K[..., 3, 0] = Qyz - Qzy
    K[..., 3, 1] = Qzx - Qxz
    K[..., 3, 2] = Qxy - Qyx
    K[..., 3, 3] = Qxx + Qyy + Qzz
    K /= 3.0
    # TODO: vectorize this -- probably could be made faster
    q = np.empty(K.shape[:-2] + (4,))
    it = np.nditer(q[..., 0], flags=['multi_index'])
    while not it.finished:
        # Use Hermitian eigenvectors, values for speed
        vals, vecs = np.linalg.eigh(K[it.multi_index])
        # Select largest eigenvector, reorder to w,x,y,z quaternion
        q[it.multi_index] = vecs[[3, 0, 1, 2], np.argmax(vals)]
        # Prefer quaternion with positive w
        # (q * -1 corresponds to same rotation as q)
        if q[it.multi_index][0] < 0:
            q[it.multi_index] *= -1
        it.iternext()
    return q

def quat2r(q):
    q = q / np.linalg.norm(q)
    x = q[..., 1]
    y = q[..., 2]
    z = q[..., 3]
    w = q[..., 0]

    R = np.zeros(q.shape[:-1]+(3, 3), dtype=np.float64)
    R[..., 0, 0] = w**2 + x**2 - y**2 - z**2
    R[..., 0, 1] = 2*x*y - 2*w*z
    R[..., 0, 2] = 2*x*z + 2*w*y
    R[..., 1, 0] = 2*x*y + 2*w*z
    R[..., 1, 1] = w**2 - x**2 + y**2 - z**2
    R[..., 1, 2] = 2*y*z - 2*w*x
    R[..., 2, 0] = 2*x*z - 2*w*y
    R[..., 2, 1] = 2*y*z + 2*w*x
    R[..., 2, 2] = w**2 - x**2 - y**2 + z**2

    return R
def skew(x):
    """ 
        Get a skew-symmetric matrix
    """
    x_hat = np.array([[0,-x[2],x[1]],[x[2],0,-x[0]],[-x[1],x[0],0]])
    return x_hat

def rodrigues(a=np.array([1,0,0]),q_rad=0.0):
    """
        Compute the rotation matrix from an angular velocity vector
    """
    a_norm = np.linalg.norm(a)
    if abs(a_norm-1) > 1e-6:
        print ("[rodrigues] norm of a should be 1.0 not [%.2e]."%(a_norm))
        return np.eye(3)
    
    a = a / a_norm
    q_rad = q_rad * a_norm
    a_hat = skew(a)
    
    R = np.eye(3) + a_hat*np.sin(q_rad) + a_hat@a_hat*(1-np.cos(q_rad))
    return R
    
def np_uv(vec):
    """
        Get unit vector
    """
    x = np.array(vec)
    return x/np.linalg.norm(x)

def get_rotation_matrix_from_two_points(p_fr,p_to):
    p_a  = np.copy(np.array([0,0,1]))
    if np.linalg.norm(p_to-p_fr) < 1e-8: # if two points are too close
        return np.eye(3)
    p_b  = (p_to-p_fr)/np.linalg.norm(p_to-p_fr)
    v    = np.cross(p_a,p_b)
    S = np.array([[0,-v[2],v[1]],[v[2],0,-v[0]],[-v[1],v[0],0]])
    if np.linalg.norm(v) == 0:
        R = np.eye(3,3)
    else:
        R = np.eye(3,3) + S + S@S*(1-np.dot(p_a,p_b))/(np.linalg.norm(v)*np.linalg.norm(v))
    return R
    

def trim_scale(x,th):
    """
        Trim scale
    """
    x         = np.copy(x)
    x_abs_max = np.abs(x).max()
    if x_abs_max > th:
        x = x*th/x_abs_max
    return x

def soft_squash(x,x_min=-1,x_max=+1,margin=0.1):
    """
        Soft squashing numpy array
    """
    def th(z,m=0.0):
        # thresholding function 
        return (m)*(np.exp(2/m*z)-1)/(np.exp(2/m*z)+1)
    x_in = np.copy(x)
    idxs_upper = np.where(x_in>(x_max-margin))
    x_in[idxs_upper] = th(x_in[idxs_upper]-(x_max-margin),m=margin) + (x_max-margin)
    idxs_lower = np.where(x_in<(x_min+margin))
    x_in[idxs_lower] = th(x_in[idxs_lower]-(x_min+margin),m=margin) + (x_min+margin)
    return x_in    

def soft_squash_multidim(
    x      = np.random.randn(100,5),
    x_min  = -np.ones(5),
    x_max  = np.ones(5),
    margin = 0.1):
    """
        Multi-dim version of 'soft_squash' function
    """
    x_squash = np.copy(x)
    dim      = x.shape[1]
    for d_idx in range(dim):
        x_squash[:,d_idx] = soft_squash(
            x=x[:,d_idx],x_min=x_min[d_idx],x_max=x_max[d_idx],margin=margin)
    return x_squash 

def kernel_se(X1,X2,hyp={'g':1,'l':1}):
    """
        Squared exponential (SE) kernel function
    """
    if len(X1.shape) == 1: X1_in = X1.reshape((-1,1))
    else: X1_in = X1.copy()
    if len(X2.shape) == 1: X2_in = X2.reshape((-1,1))
    else: X2_in = X2.copy()
    K = hyp['g']*np.exp(-cdist(X1_in,X2_in,'sqeuclidean')/(2*hyp['l']*hyp['l']))
    return K

def kernel_levse(X1,X2,L1,L2,hyp={'g':1,'l':1}):
    """
        Leveraged SE kernel function
    """
    K = hyp['g']*np.exp(-cdist(X1,X2,'sqeuclidean')/(2*hyp['l']*hyp['l']))
    L = np.cos(np.pi/2.0*cdist(L1,L2,'cityblock'))
    return np.multiply(K,L)

def is_point_in_polygon(point,polygon):
    """
        Is the point inside the polygon
    """
    if isinstance(point,np.ndarray):
        point_check = Point(point)
    else:
        point_check = point
    return sp.contains(polygon,point_check)

def is_point_feasible(point,obs_list):
    """
        Is the point feasible w.r.t. obstacle list
    """
    result = is_point_in_polygon(point,obs_list) # is the point inside each obstacle?
    if sum(result) == 0:
        return True
    else:
        return False

def is_point_to_point_connectable(point1,point2,obs_list):
    """
        Is the line connecting two points connectable
    """
    result = sp.intersects(LineString([point1,point2]),obs_list)
    if sum(result) == 0:
        return True
    else:
        return False
    
class TicTocClass(object):
    """
        Tic toc
        tictoc = TicTocClass()
        tictoc.tic()
        ~~
        tictoc.toc()
    """
    def __init__(self,name='tictoc',print_every=1):
        """
            Initialize
        """
        self.name         = name
        self.time_start   = time.time()
        self.time_end     = time.time()
        self.print_every  = print_every
        self.time_elapsed = 0.0

    def tic(self):
        """
            Tic
        """
        self.time_start = time.time()

    def toc(self,str=None,cnt=0,VERBOSE=True,RETURN=False):
        """
            Toc
        """
        self.time_end = time.time()
        self.time_elapsed = self.time_end - self.time_start
        if VERBOSE:
            if self.time_elapsed <1.0:
                time_show = self.time_elapsed*1000.0
                time_unit = 'ms'
            elif self.time_elapsed <60.0:
                time_show = self.time_elapsed
                time_unit = 's'
            else:
                time_show = self.time_elapsed/60.0
                time_unit = 'min'
            if (cnt % self.print_every) == 0:
                if str is None:
                    print ("%s Elapsed time:[%.2f]%s"%
                        (self.name,time_show,time_unit))
                else:
                    print ("%s Elapsed time:[%.2f]%s"%
                        (str,time_show,time_unit))
        if RETURN:
            return self.time_elapsed

def get_interp_const_vel_traj(traj_anchor,vel=1.0,HZ=100,ord=np.inf):
    """
        Get linearly interpolated constant velocity trajectory
    """
    L = traj_anchor.shape[0]
    D = traj_anchor.shape[1]
    dists = np.zeros(L)
    for tick in range(L):
        if tick > 0:
            p_prev,p_curr = traj_anchor[tick-1,:],traj_anchor[tick,:]
            dists[tick] = np.linalg.norm(p_prev-p_curr,ord=ord)
    times_anchor = np.cumsum(dists/vel) # [L]
    L_interp = int(times_anchor[-1]*HZ)
    times_interp = np.linspace(0,times_anchor[-1],L_interp) # [L_interp]
    traj_interp = np.zeros((L_interp,D)) # [L_interp x D]
    for d_idx in range(D):
        traj_interp[:,d_idx] = np.interp(times_interp,times_anchor,traj_anchor[:,d_idx])
    return times_interp,traj_interp

def meters2xyz(depth_img,cam_matrix):
    """
        Scaled depth image to pointcloud
    """
    fx = cam_matrix[0][0]
    cx = cam_matrix[0][2]
    fy = cam_matrix[1][1]
    cy = cam_matrix[1][2]
    
    height = depth_img.shape[0]
    width = depth_img.shape[1]
    indices = np.indices((height, width),dtype=np.float32).transpose(1,2,0)
    
    z_e = depth_img
    x_e = (indices[..., 1] - cx) * z_e / fx
    y_e = (indices[..., 0] - cy) * z_e / fy
    
    # Order of y_ e is reversed !
    xyz_img = np.stack([z_e, -x_e, -y_e], axis=-1) # [H x W x 3] 
    return xyz_img # [H x W x 3]

def compute_view_params(camera_pos,target_pos,up_vector=np.array([0,0,1])):
    """Compute azimuth, distance, elevation, and lookat for a viewer given camera pose in 3D space.

    Args:
        camera_pos (np.ndarray): 3D array of camera position.
        target_pos (np.ndarray): 3D array of target position.
        up_vector (np.ndarray): 3D array of up vector.

    Returns:
        tuple: Tuple containing azimuth, distance, elevation, and lookat values.
    """
    # Compute camera-to-target vector and distance
    cam_to_target = target_pos - camera_pos
    distance = np.linalg.norm(cam_to_target)

    # Compute azimuth and elevation
    azimuth = np.arctan2(cam_to_target[1], cam_to_target[0])
    azimuth = np.rad2deg(azimuth) # [deg]
    elevation = np.arcsin(cam_to_target[2] / distance)
    elevation = np.rad2deg(elevation) # [deg]

    # Compute lookat point
    lookat = target_pos

    # Compute camera orientation matrix
    zaxis = cam_to_target / distance
    xaxis = np.cross(up_vector, zaxis)
    yaxis = np.cross(zaxis, xaxis)
    cam_orient = np.array([xaxis, yaxis, zaxis])

    # Return computed values
    return azimuth, distance, elevation, lookat

def sample_xyzs(n_sample,x_range=[0,1],y_range=[0,1],z_range=[0,1],min_dist=0.1,xy_margin=0.0):
    """
        Sample a point in three dimensional space with the minimum distance between points
    """
    xyzs = np.zeros((n_sample,3))
    for p_idx in range(n_sample):
        while True:
            x_rand = np.random.uniform(low=x_range[0]+xy_margin,high=x_range[1]-xy_margin)
            y_rand = np.random.uniform(low=y_range[0]+xy_margin,high=y_range[1]-xy_margin)
            z_rand = np.random.uniform(low=z_range[0],high=z_range[1])
            xyz = np.array([x_rand,y_rand,z_rand])
            if p_idx == 0: break
            devc = cdist(xyz.reshape((-1,3)),xyzs[:p_idx,:].reshape((-1,3)),'euclidean')
            if devc.min() > min_dist: break # minimum distance between objects
        xyzs[p_idx,:] = xyz
    return xyzs

def create_folder_if_not_exists(file_path):
    """ 
        Create folder if not exist
    """
    folder_path = os.path.dirname(file_path)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print ("[%s] created."%(folder_path))
        
class MultiSliderClass(object):
    """
        GUI with multiple sliders
    """
    def __init__(self,
                 n_slider      = 10,
                 title         = 'Multiple Sliders',
                 window_width  = 500,
                 window_height = None,
                 x_offset      = 500,
                 y_offset      = 100,
                 slider_width  = 400,
                 label_texts   = None,
                 slider_mins   = None,
                 slider_maxs   = None,
                 slider_vals   = None,
                 resolution    = 0.1,
                 ADD_PLAYBACK  = False,
                 VERBOSE       = True
        ):
        """
            Initialze multiple sliders
        """
        self.n_slider      = n_slider
        self.title         = title
        
        self.window_width  = window_width
        if window_height is None:
            self.window_height = self.n_slider*40
        else:
            self.window_height = window_height
        self.x_offset      = x_offset
        self.y_offset      = y_offset
        self.slider_width  = slider_width
        
        self.resolution    = resolution
        self.VERBOSE       = VERBOSE
        
        # Slider values
        self.slider_values = np.zeros(self.n_slider)
        
        # Initial/default slider settings
        self.label_texts   = label_texts
        self.slider_mins   = slider_mins
        self.slider_maxs   = slider_maxs
        self.slider_vals   = slider_vals
        
        # Create main window
        self.gui = tk.Tk()
        self.gui.title("%s"%(self.title))
        self.gui.geometry("%dx%d+%d+%d"%
                          (self.window_width,self.window_height,self.x_offset,self.y_offset))
        
        # Create vertical scrollbar
        self.scrollbar = tk.Scrollbar(self.gui,orient=tk.VERTICAL)
        
        # Create a Canvas widget with the scrollbar attached
        self.canvas = tk.Canvas(self.gui,yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Configure the scrollbar to control the canvas
        self.scrollbar.config(command=self.canvas.yview)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Create a frame inside the canvas to hold the sliders
        self.sliders_frame = tk.Frame(self.canvas)
        self.canvas.create_window((0,0),window=self.sliders_frame,anchor=tk.NW)
        
        # Create sliders
        self.sliders = self.create_sliders()
        self.n_slider = len(self.sliders)
        
        # Add button for playback
        self.ADD_PLAYBACK = ADD_PLAYBACK
        if self.ADD_PLAYBACK:
            self.button_playback = tk.Button(self.gui,text="PLAY", command=self.cb_playback)
            self.button_playback.pack(pady=15)
            self.PLAYBACK = False
        
        # Update the canvas scroll region when the sliders_frame changes size
        self.sliders_frame.bind("<Configure>",self.cb_scroll)
        
        # Dummy-run to avoid some errors
        for _ in range(100): 
            self.update()
            time.sleep(1e-6)
        
    def reset_playback(self):
        self.PLAYBACK = False
        self.button_playback.config(text="PLAY")
        
    def cb_playback(self):
        """ 
            Button callback
        """
        if self.PLAYBACK:
            self.PLAYBACK = False
        else:
            self.PLAYBACK = True
        
        if self.PLAYBACK:
            self.button_playback.config(text="STOP")
        else:
            self.button_playback.config(text="PLAY")
    
    def cb_scroll(self,event):    
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        
    def cb_slider(self,slider_idx,slider_value):
        """
            Slider callback function
        """
        self.slider_values[slider_idx] = slider_value # append
        if self.VERBOSE:
            print ("slider_idx:[%d] slider_value:[%.1f]"%(slider_idx,slider_value))
        
    def create_sliders(self):
        """
            Create sliders
        """
        sliders = []
        for s_idx in range(self.n_slider):
            # Create label
            if self.label_texts is None:
                label_text = "Slider %02d "%(s_idx)
            else:
                label_text = "[%d/%d]%s"%(s_idx,self.n_slider,self.label_texts[s_idx])
            slider_label = tk.Label(self.sliders_frame, text=label_text)
            slider_label.grid(row=s_idx,column=0,padx=0,pady=0)
            
            # Create slider
            if self.slider_mins is None: slider_min = 0
            else: slider_min = self.slider_mins[s_idx]
            if self.slider_maxs is None: slider_max = 100
            else: slider_max = self.slider_maxs[s_idx]
            if self.slider_vals is None: slider_val = 50
            else: slider_val = self.slider_vals[s_idx]
            slider = tk.Scale(
                self.sliders_frame,
                from_      = slider_min,
                to         = slider_max,
                orient     = tk.HORIZONTAL,
                command    = lambda value,idx=s_idx:self.cb_slider(idx,float(value)),
                resolution = self.resolution,
                length     = self.slider_width
            )
            slider.grid(row=s_idx,column=1,padx=0,pady=0,sticky=tk.W)
            slider.set(slider_val)
            sliders.append(slider)
            
        return sliders
    
    def update(self):
        if self.is_window_exists():
            self.gui.update()
        
    def run(self):
        self.gui.mainloop()
        
    def is_window_exists(self):
        try:
            return self.gui.winfo_exists()
        except tk.TclError:
            return False
        
    def get_slider_values(self):
        return self.slider_values
    
    def set_slider_values(self,slider_values):
        """ 
            Set slider values
        """
        for s_idx in range(self.n_slider):
            if self.sliders[s_idx].get() == slider_values[s_idx]:
                DO_NOTHING = True
            else:
                self.sliders[s_idx].set(slider_values[s_idx])
    
    def close(self):
        """ 
            Close GUI
        """
        if self.is_window_exists():
            self.gui.destroy()
        else:
            print ("Window does not exist.")

def animiate_motion_with_slider(
    env,tick_slider,
    q_list,p_root_list,quat_root_list,rev_joint_names,
    PLAY_AT_START=True,
    FONTSCALE_VALUE=200):
    """
        Animate motion with slider control
    """
    # Get useful indices
    joint_idxs_fwd = env.get_idxs_fwd(joint_names=rev_joint_names)
    
    # Initialize slider
    L = q_list.shape[0]
    tick_slider.sliders[0].config(to=L)
    tick_slider.sliders[0].set(0)
    tick_slider.update()
    time.sleep(1e-1) # little delay helps
    
    # Tick slider 
    if PLAY_AT_START:
        tick_slider.PLAYBACK = True
    
    # Initialize viewer
    env.init_viewer(viewer_title='Common Rig',viewer_width=1200,viewer_height=800,
                    viewer_hide_menus=True,FONTSCALE_VALUE=FONTSCALE_VALUE)
    env.update_viewer(azimuth=152,distance=3.0,elevation=-30,lookat=[0.02,-0.03,0.8])
    env.reset()
    tick = 0
    while env.is_viewer_alive():
        # Update 
        time.sleep(1e-10) # little delay helps
        tick_slider.update()
        if tick_slider.PLAYBACK: # play mode
            tick = min(tick+1,L-1)
            if tick == (L-1): tick_slider.reset_playback()
            tick_slider.set_slider_values([tick])
        else: # stop mode
            slider_val = tick_slider.get_slider_values()
            tick = int(slider_val[0])
        # Trim tick
        if tick < 0: tick = 0
        if tick > (L-1): tick = L-1
        # FK
        q = q_list[tick,:] # [35]
        p_root = p_root_list[tick,:] # [3]
        quat_root = quat_root_list[tick,:] # [4] quaternion
        env.set_p_root(root_name='base',p=p_root)
        env.set_quat_root(root_name='base',quat=quat_root)
        env.forward(q=q,joint_idxs=joint_idxs_fwd)
        # Render
        if env.loop_every(tick_every=100) or tick_slider.PLAYBACK:
            # Plot world frame
            env.plot_T(p=np.zeros(3),R=np.eye(3,3),
                       PLOT_AXIS=True,axis_len=0.5,axis_width=0.005)
            env.plot_T(p=np.array([0,0,0.5]),R=np.eye(3,3),
                       PLOT_AXIS=False,label="tick:[%d]"%(tick))
            # Plot geometries
            env.plot_geom_T(geom_name='rfoot',axis_len=0.3)
            env.plot_geom_T(geom_name='lfoot',axis_len=0.3)
            # Plot revolute joints with arrow
            env.plot_joint_axis(axis_len=0.1,axis_r=0.01)    
            env.render()
            
    # Close MuJoCo viewer
    env.close_viewer()

def feet_anchoring(env,q_list,quat_root_list,p_root_list,rev_joint_names,
                   foot_thickness=0.04,p_cfoot_offset=np.array([0,0,0.02]),d_rf2lf_custom=0.3,
                   ik_th=1e-3,ik_iters=5000,ANIMATE_IK=True):
    """ 
        Feet anchoring
    """
    # Get useful indices
    joint_idxs_fwd = env.get_idxs_fwd(joint_names=rev_joint_names)
    joint_idxs_jac = env.get_idxs_jac(joint_names=rev_joint_names)
    
    # Initialize viewer
    if ANIMATE_IK:
        env.init_viewer(viewer_title='Common Rig',viewer_width=1200,viewer_height=800,
                        viewer_hide_menus=True,FONTSCALE_VALUE=200)
        env.update_viewer(azimuth=152,distance=3.0,elevation=-30,lookat=[0.02,-0.03,0.8])
    env.reset()

    # First, get two feet trajectories
    L = q_list.shape[0]
    p_rfoot_list = np.zeros((L,3))
    p_lfoot_list = np.zeros((L,3))
    for tick in range(L):
        # Update skeleton pose
        q = q_list[tick,:] # [35]
        p_root = p_root_list[tick,:] # [3]
        quat_root = quat_root_list[tick,:] # [4] quaternion
        env.set_p_root(root_name='base',p=p_root)
        env.set_quat_root(root_name='base',quat=quat_root)
        env.forward(q=q,joint_idxs=joint_idxs_fwd)
        # Append two feet positions
        p_rfoot_list[tick,:] = env.get_p_geom(geom_name='rfoot')
        p_lfoot_list[tick,:] = env.get_p_geom(geom_name='lfoot')

    # Modify the root position so that the center of two feet is in the origin
    p_root_centered_list = np.zeros((L,3))
    p_rfoot_centered_list = np.zeros((L,3))
    p_lfoot_centered_list = np.zeros((L,3))
    for tick in range(L):
        # Get current pose
        q = q_list[tick,:] # [35]
        p_root = p_root_list[tick,:]
        quat_root = quat_root_list[tick,:] # [4] quaternion
        # Move the body so that the feet is in the origin
        p_cfoot = 0.5*(p_rfoot_list[tick,:]+p_lfoot_list[tick,:])
        p_root_centered = p_root-p_cfoot+np.array([0,0,foot_thickness/2])
        p_root_centered_list[tick,:] = p_root_centered
        env.set_p_root(root_name='base',p=p_root_centered)
        env.set_quat_root(root_name='base',quat=quat_root)
        env.forward(q=q,joint_idxs=joint_idxs_fwd)
        # Append centered feet trajectories
        p_rfoot_centered_list[tick,:] = env.get_p_geom(geom_name='rfoot')
        p_lfoot_centered_list[tick,:] = env.get_p_geom(geom_name='lfoot')
        
    # Solve IK to anchor two feet
    p_trgt_rfoot = np.average(p_rfoot_centered_list,axis=0) # [3]
    p_trgt_lfoot = np.average(p_lfoot_centered_list,axis=0) # [3]
    p_cfoot = 0.5*(p_trgt_rfoot+p_trgt_lfoot)
    d_rf2lf = np.linalg.norm(p_trgt_rfoot-p_trgt_lfoot) # distance between feet

    # Modify feet target
    p_cfoot = p_cfoot + p_cfoot_offset
    if d_rf2lf_custom is not None:
        d_rf2lf = d_rf2lf_custom
    p_trgt_rfoot = p_cfoot - 0.5*d_rf2lf*np.array([0,1,0])
    p_trgt_lfoot = p_cfoot + 0.5*d_rf2lf*np.array([0,1,0])

    # Feet rotation target
    R_trgt_rfoot = rpy2r(np.radians([0,0,0]))
    R_trgt_lfoot = rpy2r(np.radians([0,0,0]))
    q_feetanchor_list = np.zeros((L,len(rev_joint_names)))
    for tick in range(L):
        p_root_centered = p_root_centered_list[tick,:]
        quat_root = quat_root_list[tick,:]
        q = q_list[tick,:] # [35]
        env.set_p_root(root_name='base',p=p_root_centered)
        env.set_quat_root(root_name='base',quat=quat_root)
        env.forward(q=q,joint_idxs=joint_idxs_fwd)
        # Solve IK
        ik_geom_names = ['rfoot','lfoot']
        ik_p_trgts = [p_trgt_rfoot,p_trgt_lfoot]
        ik_R_trgts = [R_trgt_rfoot,R_trgt_lfoot]
        err_traj = np.zeros(ik_iters)
        for ik_tick in range(ik_iters):
            J_list,ik_err_list = [],[]
            for ik_idx,ik_geom_name in enumerate(ik_geom_names):
                ik_p_trgt = ik_p_trgts[ik_idx]
                ik_R_trgt = ik_R_trgts[ik_idx]
                IK_P = ik_p_trgt is not None
                IK_R = ik_R_trgt is not None
                J,ik_err = env.get_ik_ingredients_geom(
                    geom_name=ik_geom_name,p_trgt=ik_p_trgt,R_trgt=ik_R_trgt,
                    IK_P=IK_P,IK_R=IK_R)
                J_list.append(J)
                ik_err_list.append(ik_err)
            J_stack      = np.vstack(J_list)
            ik_err_stack = np.hstack(ik_err_list)
            ik_err_norm = np.linalg.norm(ik_err_stack)
            err_traj[ik_tick] = ik_err_norm
            dq = env.damped_ls(J_stack,ik_err_stack,stepsize=1,eps=1e-3,th=np.radians(10.0))
            q = q + dq[joint_idxs_jac]
            env.forward(q=q,joint_idxs=joint_idxs_fwd)
            # Early terminate
            if ik_err_norm < ik_th: break
            # Error
            if ik_tick == (ik_iters-1):
                print ("[feet_anchoring] ik_tick:[%d] ik_err_norm:[%.3f] is above threshold:[%.3f]"%
                       (ik_tick,ik_err_norm,ik_th))
            
        # Append q
        q_feetanchor_list[tick,:] = env.get_qpos_joints(rev_joint_names)
        
        # IK Debug plot
        if ANIMATE_IK:
            env.plot_T(p=np.zeros(3),R=np.eye(3,3),
                    PLOT_AXIS=True,axis_len=0.5,axis_width=0.01)
            env.plot_T(p=np.array([0,0,0.5]),R=np.eye(3,3),
                    PLOT_AXIS=False,label="tick:[%d/%d]"%(tick,L))
            env.plot_ik_geom_info(
                ik_geom_names,ik_p_trgts,ik_R_trgts,axis_len=0.2,axis_width=0.01,sphere_r=0.1)
            env.render()
        
    # Close MuJoCo viewer
    if ANIMATE_IK:
        env.close_viewer()
    
    # Return
    feet_anchor_res = {
        'L':L,
        'p_root_centered_list':p_root_centered_list,
        'q_feetanchor_list':q_feetanchor_list}
    return feet_anchor_res

def blend_tween_trajectories(
    time_blend_list,intv_fade,
    time_a_list,x_a_list,time_b_list,x_b_list,
    time_tween_list,x_tween_list):
    """ 
        Blen trajectory a, trajectory b, and tweened trajectory
    """
    # Buffers to save blended results
    dim_x        = x_a_list.shape[1]
    n_blend      = time_blend_list.shape[0]
    x_blend_list = np.zeros((n_blend,dim_x))
    
    # Append
    time_ab_list = np.concatenate((time_a_list,time_b_list))
    x_ab_list    = np.vstack((x_a_list,x_b_list))
    
    # Initialize 'x_blend_list' with nearest interpolation of 'x_ab_list'
    for d_idx in range(dim_x):
        for tick in range(len(time_blend_list)):
            time_curr = time_blend_list[tick]
            idx_min = np.argmin(np.abs(time_ab_list-time_curr))
            x_blend_list[tick,d_idx] = x_ab_list[idx_min,d_idx]
            
    # Blend trajectories
    for d_idx in range(dim_x):
        
        # First blending (centered at the end of motion a)
        time_center = time_a_list[-1]
        time_min    = time_center - intv_fade
        time_max    = time_center + intv_fade
        for tick in range(n_blend):
            time_curr = time_blend_list[tick]
            if (time_min < time_curr) and (time_curr <= time_max):
                alpha = (time_curr-time_min)/(time_max-time_min)
                idx_a = np.argmin(np.abs(time_a_list-time_curr))
                idx_tween = np.argmin(np.abs(time_tween_list-time_curr))
                # Update
                x_blend_list[tick,d_idx] = (1-alpha)*x_a_list[idx_a,d_idx] + \
                    alpha*x_tween_list[idx_tween,d_idx]
                
        # Second blending (centered at the start of motion b)
        time_center = time_b_list[0]
        time_min    = time_center - intv_fade
        time_max    = time_center + intv_fade
        for tick in range(n_blend):
            time_curr = time_blend_list[tick]
            if (time_min < time_curr) and (time_curr <= time_max):
                alpha = (time_curr-time_min)/(time_max-time_min)
                idx_b = np.argmin(np.abs(time_b_list-time_curr))
                idx_tween = np.argmin(np.abs(time_tween_list-time_curr))
                # Update
                x_blend_list[tick,d_idx] = alpha*x_b_list[idx_b,d_idx] + \
                    (1-alpha)*x_tween_list[idx_tween,d_idx]
                
        # Third blending (nearest filtering between the end of a and the start of b)
        for tick in range(n_blend):
            time_curr = time_blend_list[tick]
            if ((time_a_list[-1]+intv_fade) < time_curr) and (time_curr <= (time_b_list[0]-intv_fade)):
                idx_tween = np.argmin(np.abs(time_tween_list-time_curr))
                # Update
                x_blend_list[tick,d_idx] = x_tween_list[idx_tween,d_idx]

    # Return
    return x_blend_list

def get_gp_mean_function(time_in_list,x_in_list,time_out_list,
                         hyp={'g':1.0,'l':1.0},sig2w=1e-8):
    """ 
        Gaussian process mean
    """
    n_in,n_out  = time_in_list.shape[0],time_out_list.shape[0]
    K_in        = kernel_se(time_in_list,time_in_list,hyp=hyp)
    K_tween_in  = kernel_se(time_out_list,time_in_list,hyp=hyp)
    inv_K_ab    = np.linalg.inv(K_in+sig2w*np.eye(n_in))
    mu_x_in     = np.mean(x_in_list,axis=0)
    x_out_list  = K_tween_in @ inv_K_ab @ (x_in_list-mu_x_in) + mu_x_in # GP result
    return x_out_list
    
def animate_motion_with_media(env,p_root_list,quat_root_list,q_list,rev_joint_names,HZ,
                              viewer_distance=3.0):
    """
        Animate motion with media.show_video
    """
    # Initialize viewer
    L              = q_list.shape[0]
    joint_idxs_fwd = env.get_idxs_fwd(joint_names=rev_joint_names)
    env.init_viewer(viewer_title='Common Rig',viewer_width=1200,viewer_height=800,
                    viewer_hide_menus=True,FONTSCALE_VALUE=200)
    env.update_viewer(azimuth=152,distance=viewer_distance,elevation=-30,lookat=[0.02,-0.03,0.8])
    env.reset()
    img_list = []
    for tick in range(L):
        # FK
        q         = q_list[tick,:] # [35]
        p_root    = p_root_list[tick,:] # [3]
        quat_root = quat_root_list[tick,:] # [4] quaternion
        env.set_p_root(root_name='base',p=p_root)
        env.set_quat_root(root_name='base',quat=quat_root)
        env.forward(q=q,joint_idxs=joint_idxs_fwd)
        # Render
        env.plot_T(p=np.zeros(3),R=np.eye(3,3),
                PLOT_AXIS=True,axis_len=0.5,axis_width=0.005)
        env.plot_T(p=np.array([0,0,0.5]),R=np.eye(3,3),
                PLOT_AXIS=False,label="tick:[%d]"%(tick))
        env.plot_geom_T(geom_name='rfoot',axis_len=0.3)
        env.plot_geom_T(geom_name='lfoot',axis_len=0.3)
        env.plot_joint_axis(axis_len=0.1,axis_r=0.01)    
        env.render()
        # Append image
        img = env.grab_image()
        img_list.append(img)
    # Close MuJoCo viewer
    env.close_viewer()
    # Make video
    media.show_video(img_list,fps=HZ)

### extra functions

def rpy2R(r0, order=[0,1,2]):
    c1 = np.math.cos(r0[0]); c2 = np.math.cos(r0[1]); c3 = np.math.cos(r0[2])
    s1 = np.math.sin(r0[0]); s2 = np.math.sin(r0[1]); s3 = np.math.sin(r0[2])

    a1 = np.array([[1,0,0],[0,c1,-s1],[0,s1,c1]])
    a2 = np.array([[c2,0,s2],[0,1,0],[-s2,0,c2]])
    a3 = np.array([[c3,-s3,0],[s3,c3,0],[0,0,1]])

    a_list = [a1,a2,a3]
    a = np.matmul(np.matmul(a_list[order[0]],a_list[order[1]]),a_list[order[2]])

    assert a.shape == (3,3)
    return a

def get_uv_dict_em(p):
    uv_dict = {}

    # Upper Body
    uv_dict['root2spine'] = np_uv(p[11,:] - p[0,:])
    uv_dict['spine2neck'] = np_uv(p[13,:] - p[11,:])
    uv_dict['neck2rs'] = np_uv(p[17,:] - p[13,:])
    uv_dict['rs2re'] = np_uv(p[18,:] - p[17,:])
    uv_dict['re2rw'] = np_uv(p[19,:] - p[18,:])
    uv_dict['neck2ls'] = np_uv(p[45,:] - p[13,:])
    uv_dict['ls2le'] = np_uv(p[46,:] - p[45,:])
    uv_dict['le2lw'] = np_uv(p[47,:] - p[46,:])

    # Lower Body
    uv_dict['root2rp'] = np_uv(p[1,:] - p[0,:])
    uv_dict['rp2rk'] = np_uv(p[2,:] - p[1,:])
    uv_dict['rk2ra'] = np_uv(p[3,:] - p[2,:])
    uv_dict['root2lp'] = np_uv(p[5,:] - p[0,:])
    uv_dict['lp2lk'] = np_uv(p[6,:] - p[5,:])
    uv_dict['lk2la'] = np_uv(p[7,:] - p[6,:])

    # Right Hand
    uv_dict['rw2r1meta'] = np_uv(p[20,:] - p[19,:])
    uv_dict['rw2r2meta'] = np_uv(p[24,:] - p[19,:])
    uv_dict['rw2r3meta'] = np_uv(p[29,:] - p[19,:])
    uv_dict['rw2r4meta'] = np_uv(p[34,:] - p[19,:])
    uv_dict['rw2r5meta'] = np_uv(p[39,:] - p[19,:])
    ## Thumb
    uv_dict['r1meta2r1prox'] = np_uv(p[21,:] - p[20,:])
    uv_dict['r1prox2r1dist'] = np_uv(p[22,:] - p[21,:])
    uv_dict['r1dist2r1tip'] = np_uv(p[23,:] - p[22,:])
    ## Index
    uv_dict['r2meta2r2prox'] = np_uv(p[25,:] - p[24,:])
    uv_dict['r2prox2r2med'] = np_uv(p[26,:] - p[25,:])
    uv_dict['r2med2r2dist'] = np_uv(p[27,:] - p[26,:])
    uv_dict['r2dist2r2tip'] = np_uv(p[28,:] - p[27,:])
    ## Middle
    uv_dict['r3meta2r3prox'] = np_uv(p[30,:] - p[29,:])
    uv_dict['r3prox2r3med'] = np_uv(p[31,:] - p[30,:])
    uv_dict['r3med2r3dist'] = np_uv(p[32,:] - p[31,:])
    uv_dict['r3dist2r3tip'] = np_uv(p[33,:] - p[32,:])
    ## Ring
    uv_dict['r4meta2r4prox'] = np_uv(p[35,:] - p[34,:])
    uv_dict['r4prox2r4med'] = np_uv(p[36,:] - p[35,:])
    uv_dict['r4med2r4dist'] = np_uv(p[37,:] - p[36,:])
    uv_dict['r4dist2r4tip'] = np_uv(p[38,:] - p[37,:])
    ## Pinky
    uv_dict['r5meta2r5prox'] = np_uv(p[40,:] - p[39,:])
    uv_dict['r5prox2r5med'] = np_uv(p[41,:] - p[40,:])
    uv_dict['r5med2r5dist'] = np_uv(p[42,:] - p[41,:])
    uv_dict['r5dist2r5tip'] = np_uv(p[43,:] - p[42,:])

    # Left Hand
    uv_dict['lw2l1meta'] = np_uv(p[48,:] - p[47,:])
    uv_dict['lw2l2meta'] = np_uv(p[52,:] - p[47,:])
    uv_dict['lw2l3meta'] = np_uv(p[57,:] - p[47,:])
    uv_dict['lw2l4meta'] = np_uv(p[62,:] - p[47,:])
    uv_dict['lw2l5meta'] = np_uv(p[67,:] - p[47,:])
    ## Thumb
    uv_dict['l1meta2l1prox'] = np_uv(p[49,:] - p[48,:])
    uv_dict['l1prox2l1dist'] = np_uv(p[50,:] - p[49,:])
    uv_dict['l1dist2l1tip'] = np_uv(p[51,:] - p[50,:])
    ## Index
    uv_dict['l2meta2l2prox'] = np_uv(p[53,:] - p[52,:])
    uv_dict['l2prox2l2med'] = np_uv(p[54,:] - p[53,:])
    uv_dict['l2med2l2dist'] = np_uv(p[55,:] - p[54,:])
    uv_dict['l2dist2l2tip'] = np_uv(p[56,:] - p[55,:])
    ## Middle
    uv_dict['l3meta2l3prox'] = np_uv(p[58,:] - p[57,:])
    uv_dict['l3prox2l3med'] = np_uv(p[59,:] - p[58,:])
    uv_dict['l3med2l3dist'] = np_uv(p[60,:] - p[59,:])
    uv_dict['l3dist2l3tip'] = np_uv(p[61,:] - p[60,:])
    ## Ring
    uv_dict['l4meta2l4prox'] = np_uv(p[63,:] - p[62,:])
    uv_dict['l4prox2l4med'] = np_uv(p[64,:] - p[63,:])
    uv_dict['l4med2l4dist'] = np_uv(p[65,:] - p[64,:])
    uv_dict['l4dist2l4tip'] = np_uv(p[66,:] - p[65,:])
    ## Pinky
    uv_dict['l5meta2l5prox'] = np_uv(p[68,:] - p[67,:])
    uv_dict['l5prox2l5med'] = np_uv(p[69,:] - p[68,:])
    uv_dict['l5med2l5dist'] = np_uv(p[70,:] - p[69,:])
    uv_dict['l5dist2l5tip'] = np_uv(p[71,:] - p[70,:])

    return uv_dict

def get_p_target_em(p, uv_dict):
    len_rig = {}
    len_rig['root2spine'] = 0.1990
    len_rig['spine2neck'] = 0.1990
    len_rig['neck2rs'] = 0.1809
    len_rig['rs2re'] = 0.2768
    len_rig['re2rw'] = 0.1815
    len_rig['neck2ls'] = 0.1809
    len_rig['ls2le'] = 0.2768
    len_rig['le2lw'] = 0.1815
    len_rig['root2rp'] = 0.1357
    len_rig['rp2rk'] = 0.4049
    len_rig['rk2ra'] = 0.4057
    len_rig['root2lp'] = 0.1357
    len_rig['lp2lk'] = 0.4049
    len_rig['lk2la'] = 0.4057

    p_target = {}
    p_target['right_pelvis'] = p[0,:] + len_rig['root2rp'] * uv_dict['root2rp']
    p_target['right_knee'] = p_target['right_pelvis'] + len_rig['rp2rk'] * uv_dict['rp2rk']
    p_target['right_ankle'] = p_target['right_knee'] + len_rig['rk2ra'] * uv_dict['rk2ra']
    p_target['left_pelvis'] = p[0,:] + len_rig['root2lp'] * uv_dict['root2lp']
    p_target['left_knee'] = p_target['left_pelvis'] + len_rig['lp2lk'] * uv_dict['lp2lk']
    p_target['left_ankle'] = p_target['left_knee'] + len_rig['lk2la'] * uv_dict['lk2la']
    p_target['spine'] = p[0,:] + len_rig['root2spine'] * uv_dict['root2spine']
    p_target['neck'] = p_target['spine'] + len_rig['spine2neck'] * uv_dict['spine2neck']
    p_target['right_shoulder'] = p_target['neck'] + len_rig['neck2rs'] * uv_dict['neck2rs']
    p_target['right_elbow'] = p_target['right_shoulder'] + len_rig['rs2re'] * uv_dict['rs2re']
    p_target['right_hand'] = p_target['right_elbow'] + len_rig['re2rw'] * uv_dict['re2rw']
    p_target['left_shoulder'] = p_target['neck'] + len_rig['neck2ls'] * uv_dict['neck2ls']
    p_target['left_elbow'] = p_target['left_shoulder'] + len_rig['ls2le'] * uv_dict['ls2le']
    p_target['left_hand'] = p_target['left_elbow'] + len_rig['le2lw'] * uv_dict['le2lw']

    len_rig['rw2r1meta'] = 0.0411049
    len_rig['rw2r2meta'] = 0.0392563
    len_rig['rw2r3meta'] = 0.0360309
    len_rig['rw2r4meta'] = 0.0350433
    len_rig['rw2r5meta'] = 0.0351108

    len_rig['r1meta2r1prox'] = 0.03788
    len_rig['r1prox2r1dist'] = 0.02631
    len_rig['r1dist2r1tip'] = 0.02256

    len_rig['r2meta2r2prox'] = 0.0546439
    len_rig['r2prox2r2med'] = 0.03723
    len_rig['r2med2r2dist'] = 0.02111
    len_rig['r2dist2r2tip'] = 0.01857

    len_rig['r3meta2r3prox'] = 0.0533249
    len_rig['r3prox2r3med'] = 0.04062
    len_rig['r3med2r3dist'] = 0.02547
    len_rig['r3dist2r3tip'] = 0.02031

    len_rig['r4meta2r4prox'] = 0.0479248
    len_rig['r4prox2r4med'] = 0.03541
    len_rig['r4med2r4dist'] = 0.02456
    len_rig['r4dist2r4tip'] = 0.01910

    len_rig['r5meta2r5prox'] = 0.0440437
    len_rig['r5prox2r5med'] = 0.02835
    len_rig['r5med2r5dist'] = 0.01792
    len_rig['r5dist2r5tip'] = 0.01692

    len_rig['lw2l1meta'] = 0.0411049
    len_rig['lw2l2meta'] = 0.0392563
    len_rig['lw2l3meta'] = 0.0360309
    len_rig['lw2l4meta'] = 0.0350433
    len_rig['lw2l5meta'] = 0.0351108

    len_rig['l1meta2l1prox'] = 0.03788
    len_rig['l1prox2l1dist'] = 0.02631
    len_rig['l1dist2l1tip'] = 0.02256

    len_rig['l2meta2l2prox'] = 0.0546439
    len_rig['l2prox2l2med'] = 0.03723
    len_rig['l2med2l2dist'] = 0.02111
    len_rig['l2dist2l2tip'] = 0.01857

    len_rig['l3meta2l3prox'] = 0.0533249
    len_rig['l3prox2l3med'] = 0.04062
    len_rig['l3med2l3dist'] = 0.02547
    len_rig['l3dist2l3tip'] = 0.02031

    len_rig['l4meta2l4prox'] = 0.0479248
    len_rig['l4prox2l4med'] = 0.03541
    len_rig['l4med2l4dist'] = 0.02456
    len_rig['l4dist2l4tip'] = 0.01910

    len_rig['l5meta2l5prox'] = 0.0440437
    len_rig['l5prox2l5med'] = 0.02835
    len_rig['l5med2l5dist'] = 0.01792
    len_rig['l5dist2l5tip'] = 0.01692

    p_target['rh_meta_1'] = p_target['right_hand'] + len_rig['rw2r1meta'] * uv_dict['rw2r1meta']
    p_target['rh_meta_2'] = p_target['right_hand'] + len_rig['rw2r2meta'] * uv_dict['rw2r2meta']
    p_target['rh_meta_3'] = p_target['right_hand'] + len_rig['rw2r3meta'] * uv_dict['rw2r3meta']
    p_target['rh_meta_4'] = p_target['right_hand'] + len_rig['rw2r4meta'] * uv_dict['rw2r4meta']
    p_target['rh_meta_5'] = p_target['right_hand'] + len_rig['rw2r5meta'] * uv_dict['rw2r5meta']
    
    p_target['rh_prox_1'] = p_target['rh_meta_1'] + len_rig['r1meta2r1prox'] * uv_dict['r1meta2r1prox']
    p_target['rh_dist_1'] = p_target['rh_prox_1'] + len_rig['r1prox2r1dist'] * uv_dict['r1prox2r1dist']
    p_target['rh_tip_1'] = p_target['rh_dist_1'] + len_rig['r1dist2r1tip'] * uv_dict['r1dist2r1tip']

    p_target['rh_prox_2'] = p_target['rh_meta_2'] + len_rig['r2meta2r2prox'] * uv_dict['r2meta2r2prox']
    p_target['rh_med_2'] = p_target['rh_prox_2'] + len_rig['r2prox2r2med'] * uv_dict['r2prox2r2med']
    p_target['rh_dist_2'] = p_target['rh_med_2'] + len_rig['r2med2r2dist'] * uv_dict['r2med2r2dist']
    p_target['rh_tip_2'] = p_target['rh_dist_2'] + len_rig['r2dist2r2tip'] * uv_dict['r2dist2r2tip']

    p_target['rh_prox_3'] = p_target['rh_meta_3'] + len_rig['r3meta2r3prox'] * uv_dict['r3meta2r3prox']
    p_target['rh_med_3'] = p_target['rh_prox_3'] + len_rig['r3prox2r3med'] * uv_dict['r3prox2r3med']
    p_target['rh_dist_3'] = p_target['rh_med_3'] + len_rig['r3med2r3dist'] * uv_dict['r3med2r3dist']
    p_target['rh_tip_3'] = p_target['rh_dist_3'] + len_rig['r3dist2r3tip'] * uv_dict['r3dist2r3tip']

    p_target['rh_prox_4'] = p_target['rh_meta_4'] + len_rig['r4meta2r4prox'] * uv_dict['r4meta2r4prox']
    p_target['rh_med_4'] = p_target['rh_prox_4'] + len_rig['r4prox2r4med'] * uv_dict['r4prox2r4med']
    p_target['rh_dist_4'] = p_target['rh_med_4'] + len_rig['r4med2r4dist'] * uv_dict['r4med2r4dist']
    p_target['rh_tip_4'] = p_target['rh_dist_4'] + len_rig['r4dist2r4tip'] * uv_dict['r4dist2r4tip']

    p_target['rh_prox_5'] = p_target['rh_meta_5'] + len_rig['r5meta2r5prox'] * uv_dict['r5meta2r5prox']
    p_target['rh_med_5'] = p_target['rh_prox_5'] + len_rig['r5prox2r5med'] * uv_dict['r5prox2r5med']
    p_target['rh_dist_5'] = p_target['rh_med_5'] + len_rig['r5med2r5dist'] * uv_dict['r5med2r5dist']
    p_target['rh_tip_5'] = p_target['rh_dist_5'] + len_rig['r5dist2r5tip'] * uv_dict['r5dist2r5tip']

    p_target['lh_meta_1'] = p_target['left_hand'] + len_rig['lw2l1meta'] * uv_dict['lw2l1meta']
    p_target['lh_meta_2'] = p_target['left_hand'] + len_rig['lw2l2meta'] * uv_dict['lw2l2meta']
    p_target['lh_meta_3'] = p_target['left_hand'] + len_rig['lw2l3meta'] * uv_dict['lw2l3meta']
    p_target['lh_meta_4'] = p_target['left_hand'] + len_rig['lw2l4meta'] * uv_dict['lw2l4meta']
    p_target['lh_meta_5'] = p_target['left_hand'] + len_rig['lw2l5meta'] * uv_dict['lw2l5meta']

    p_target['lh_prox_1'] = p_target['lh_meta_1'] + len_rig['l1meta2l1prox'] * uv_dict['l1meta2l1prox']
    p_target['lh_dist_1'] = p_target['lh_prox_1'] + len_rig['l1prox2l1dist'] * uv_dict['l1prox2l1dist']
    p_target['lh_tip_1'] = p_target['lh_dist_1'] + len_rig['l1dist2l1tip'] * uv_dict['l1dist2l1tip']

    p_target['lh_prox_2'] = p_target['lh_meta_2'] + len_rig['l2meta2l2prox'] * uv_dict['l2meta2l2prox']
    p_target['lh_med_2'] = p_target['lh_prox_2'] + len_rig['l2prox2l2med'] * uv_dict['l2prox2l2med']
    p_target['lh_dist_2'] = p_target['lh_med_2'] + len_rig['l2med2l2dist'] * uv_dict['l2med2l2dist']
    p_target['lh_tip_2'] = p_target['lh_dist_2'] + len_rig['l2dist2l2tip'] * uv_dict['l2dist2l2tip']

    p_target['lh_prox_3'] = p_target['lh_meta_3'] + len_rig['l3meta2l3prox'] * uv_dict['l3meta2l3prox']
    p_target['lh_med_3'] = p_target['lh_prox_3'] + len_rig['l3prox2l3med'] * uv_dict['l3prox2l3med']
    p_target['lh_dist_3'] = p_target['lh_med_3'] + len_rig['l3med2l3dist'] * uv_dict['l3med2l3dist']
    p_target['lh_tip_3'] = p_target['lh_dist_3'] + len_rig['l3dist2l3tip'] * uv_dict['l3dist2l3tip']

    p_target['lh_prox_4'] = p_target['lh_meta_4'] + len_rig['l4meta2l4prox'] * uv_dict['l4meta2l4prox']
    p_target['lh_med_4'] = p_target['lh_prox_4'] + len_rig['l4prox2l4med'] * uv_dict['l4prox2l4med']
    p_target['lh_dist_4'] = p_target['lh_med_4'] + len_rig['l4med2l4dist'] * uv_dict['l4med2l4dist']
    p_target['lh_tip_4'] = p_target['lh_dist_4'] + len_rig['l4dist2l4tip'] * uv_dict['l4dist2l4tip']

    p_target['lh_prox_5'] = p_target['lh_meta_5'] + len_rig['l5meta2l5prox'] * uv_dict['l5meta2l5prox']
    p_target['lh_med_5'] = p_target['lh_prox_5'] + len_rig['l5prox2l5med'] * uv_dict['l5prox2l5med']
    p_target['lh_dist_5'] = p_target['lh_med_5'] + len_rig['l5med2l5dist'] * uv_dict['l5med2l5dist']
    p_target['lh_tip_5'] = p_target['lh_dist_5'] + len_rig['l5dist2l5tip'] * uv_dict['l5dist2l5tip']

    return p_target

def get_uv_dict_myohuman(p):
    uv_dict = {}

    # Lower Body
    uv_dict['pelvis2femur_r'] = np_uv(p[1,:] - p[0,:])
    uv_dict['femur_r2tibia_r'] = np_uv(p[2,:] - p[1,:])
    uv_dict['tibia_r2talus_r'] = np_uv(p[3,:] - p[2,:])
    uv_dict['pelvis2femur_l'] = np_uv(p[5,:] - p[0,:])
    uv_dict['femur_l2tibia_l'] = np_uv(p[6,:] - p[5,:])
    uv_dict['tibia_l2talus_l'] = np_uv(p[7,:] - p[6,:])
    
    # Upper Body
    uv_dict['pelvis2torso'] = np_uv(p[9,:] - p[0,:])
    uv_dict['torso2humerus_r'] = np_uv(p[17,:] - p[9,:])
    uv_dict['humerus_r2radius'] = np_uv(p[18,:] - p[17,:])
    uv_dict['radius2lunate'] = np_uv(p[19,:] - p[18,:])
    uv_dict['torso2humerus_l'] = np_uv(p[45,:] - p[9,:])
    uv_dict['humerus_l2radius_l'] = np_uv(p[46,:] - p[45,:])
    uv_dict['radius_l2lunate_l'] = np_uv(p[47,:] - p[46,:])

    return uv_dict

def get_p_target_myohuman(p, uv_dict):
    len_rig = {}
    len_rig['pelvis2femur_r'] = 0.12368013533304369
    len_rig['femur_r2tibia_r'] = 0.4044269792040081
    len_rig['tibia_r2talus_r'] = 0.4001249804748512
    len_rig['pelvis2femur_l'] = 0.12368013533304369
    len_rig['femur_l2tibia_l'] = 0.4044269792040081
    len_rig['tibia_l2talus_l'] = 0.4001249804748512
    len_rig['pelvis2torso'] = 0.12954821496261537
    len_rig['torso2humerus_r'] = 0.408561138662257
    len_rig['humerus_r2radius'] = 0.3003331483536241
    len_rig['radius2lunate'] = 0.2439528642996429
    len_rig['torso2humerus_l'] = 0.408561138662257
    len_rig['humerus_l2radius_l'] = 0.3003331483536241
    len_rig['radius_l2lunate_l'] = 0.2439528642996429

    p_target = {}
    p_target['femur_r'] = p[0,:] + len_rig['pelvis2femur_r'] * uv_dict['pelvis2femur_r']
    p_target['tibia_r'] = p_target['femur_r'] + len_rig['femur_r2tibia_r'] * uv_dict['femur_r2tibia_r']
    p_target['talus_r'] = p_target['tibia_r'] + len_rig['tibia_r2talus_r'] * uv_dict['tibia_r2talus_r']
    p_target['femur_l'] = p[0,:] + len_rig['pelvis2femur_l'] * uv_dict['pelvis2femur_l']
    p_target['tibia_l'] = p_target['femur_l'] + len_rig['femur_l2tibia_l'] * uv_dict['femur_l2tibia_l']
    p_target['talus_l'] = p_target['tibia_l'] + len_rig['tibia_l2talus_l'] * uv_dict['tibia_l2talus_l']
    p_target['torso'] = p[0,:] + len_rig['pelvis2torso'] * uv_dict['pelvis2torso']
    p_target['humerus_r'] = p_target['torso'] + len_rig['torso2humerus_r'] * uv_dict['torso2humerus_r']
    p_target['radius'] = p_target['humerus_r'] + len_rig['humerus_r2radius'] * uv_dict['humerus_r2radius']
    p_target['lunate'] = p_target['radius'] + len_rig['radius2lunate'] * uv_dict['radius2lunate']
    p_target['humerus_l'] = p_target['torso'] + len_rig['torso2humerus_l'] * uv_dict['torso2humerus_l']
    p_target['radius_l'] = p_target['humerus_l'] + len_rig['humerus_l2radius_l'] * uv_dict['humerus_l2radius_l']
    p_target['lunate_l'] = p_target['radius_l'] + len_rig['radius_l2lunate_l'] * uv_dict['radius_l2lunate_l']
    
    return p_target

def get_uv_dict_smpl(p):
    uv_dict = {}

    # Lower Body
    # uv_dict['pelvis2right_hip'] = np_uv(p[1,:] - p[0,:])
    # uv_dict['right_hip2right_knee'] = np_uv(p[2,:] - p[1,:])
    # uv_dict['right_knee2right_ankle'] = np_uv(p[3,:] - p[2,:])
    # uv_dict['pelvis2left_hip'] = np_uv(p[5,:] - p[0,:])
    # uv_dict['left_hip2left_knee'] = np_uv(p[6,:] - p[5,:])
    # uv_dict['left_knee2left_ankle'] = np_uv(p[7,:] - p[6,:])

    uv_dict['base2right_pelvis'] = np_uv(p[68,:] - p[0,:])
    uv_dict['right_pelvis2right_knee'] = np_uv(p[69,:] - p[68,:])
    uv_dict['right_knee2right_ankle'] = np_uv(p[70,:] - p[69,:])
    uv_dict['base2left_pelvis'] = np_uv(p[64,:] - p[0,:])
    uv_dict['left_pelvis2left_knee'] = np_uv(p[65,:] - p[64,:])
    uv_dict['left_knee2left_ankle'] = np_uv(p[66,:] - p[65,:])
    
    # Upper Body
    # uv_dict['pelvis2spine'] = np_uv(p[9,:] - p[0,:])
    # uv_dict['spine2right_shoulder'] = np_uv(p[17,:] - p[9,:])
    # uv_dict['right_shoulder2right_elbow'] = np_uv(p[18,:] - p[17,:])
    # uv_dict['right_elbow2right_wrist'] = np_uv(p[19,:] - p[18,:])
    # uv_dict['spine2left_shoulder'] = np_uv(p[45,:] - p[9,:])
    # uv_dict['left_shoulder2left_elbow'] = np_uv(p[46,:] - p[45,:])
    # uv_dict['left_elbow2left_wrist'] = np_uv(p[47,:] - p[46,:])

    uv_dict['base2spine'] = np_uv(p[1,:] - p[0,:])
    uv_dict['spine2right_shoulder'] = np_uv(p[34,:] - p[1,:])
    uv_dict['right_shoulder2right_elbow'] = np_uv(p[35,:] - p[34,:])
    uv_dict['right_elbow2right_wrist'] = np_uv(p[36,:] - p[35,:])
    uv_dict['spine2left_shoulder'] = np_uv(p[6,:] - p[1,:])
    uv_dict['left_shoulder2left_elbow'] = np_uv(p[7,:] - p[6,:])
    uv_dict['left_elbow2left_wrist'] = np_uv(p[8,:] - p[7,:])

    return uv_dict

def get_uv_dict_smpl_cmu(r):
    uv_dict = {}

    # Lower Body
    # uv_dict['pelvis2right_hip'] = np_uv(p[1,:] - p[0,:])
    # uv_dict['right_hip2right_knee'] = np_uv(p[2,:] - p[1,:])
    # uv_dict['right_knee2right_ankle'] = np_uv(p[3,:] - p[2,:])
    # uv_dict['pelvis2left_hip'] = np_uv(p[5,:] - p[0,:])
    # uv_dict['left_hip2left_knee'] = np_uv(p[6,:] - p[5,:])
    # uv_dict['left_knee2left_ankle'] = np_uv(p[7,:] - p[6,:])

    uv_dict['base2right_pelvis'] = np_uv(r[27]) # np_uv(p[68,:] - p[0,:])
    uv_dict['right_pelvis2right_knee'] = np_uv(r[28]) # np_uv(p[69,:] - p[68,:])
    uv_dict['right_knee2right_ankle'] = np_uv(r[29]) # np_uv(p[70,:] - p[69,:])
    uv_dict['base2left_pelvis'] = np_uv(r[33]) # np_uv(p[64,:] - p[0,:])
    uv_dict['left_pelvis2left_knee'] = np_uv(r[34]) # np_uv(p[65,:] - p[64,:])
    uv_dict['left_knee2left_ankle'] = np_uv(r[35]) # np_uv(p[66,:] - p[65,:])
    
    # Upper Body
    # uv_dict['pelvis2spine'] = np_uv(p[9,:] - p[0,:])
    # uv_dict['spine2right_shoulder'] = np_uv(p[17,:] - p[9,:])
    # uv_dict['right_shoulder2right_elbow'] = np_uv(p[18,:] - p[17,:])
    # uv_dict['right_elbow2right_wrist'] = np_uv(p[19,:] - p[18,:])
    # uv_dict['spine2left_shoulder'] = np_uv(p[45,:] - p[9,:])
    # uv_dict['left_shoulder2left_elbow'] = np_uv(p[46,:] - p[45,:])
    # uv_dict['left_elbow2left_wrist'] = np_uv(p[47,:] - p[46,:])

    uv_dict['base2spine'] = np_uv(r[3]) # np_uv(p[1,:] - p[0,:])
    uv_dict['spine2right_shoulder'] = np_uv(r[5]) # np_uv(p[34,:] - p[1,:])
    uv_dict['right_shoulder2right_elbow'] = np_uv(r[6]) # np_uv(p[35,:] - p[34,:])
    uv_dict['right_elbow2right_wrist'] = np_uv(r[7]) # np_uv(p[36,:] - p[35,:])
    uv_dict['spine2left_shoulder'] = np_uv(r[14]) # np_uv(p[6,:] - p[1,:])
    uv_dict['left_shoulder2left_elbow'] = np_uv(r[15]) # np_uv(p[7,:] - p[6,:])
    uv_dict['left_elbow2left_wrist'] = np_uv(r[16]) # np_uv(p[8,:] - p[7,:])

    return uv_dict


def get_p_target_smpl(p, uv_dict):
    len_rig = {}
    len_rig['base2right_pelvis'] = 0.11504147358444483
    len_rig['right_pelvis2right_knee'] = 0.37678782215893153
    len_rig['right_knee2right_ankle'] = 0.40058266700996176
    len_rig['base2left_pelvis'] = 0.11504147358444483
    len_rig['left_pelvis2left_knee'] = 0.37678782215893153
    len_rig['left_knee2left_ankle'] = 0.40058266700996176
    len_rig['base2spine'] = 0.1122145063476423
    len_rig['spine2right_shoulder'] = 0.38384200442376404
    len_rig['right_shoulder2right_elbow'] = 0.261372309763501
    len_rig['right_elbow2right_wrist'] = 0.24939829355374343
    len_rig['spine2left_shoulder'] = 0.38384200442376404
    len_rig['left_shoulder2left_elbow'] = 0.261372309763501
    len_rig['left_elbow2left_wrist'] = 0.24939829355374343

    p_target = {}
    p_target['right_pelvis'] = p[0,:] + len_rig['base2right_pelvis'] * uv_dict['base2right_pelvis']
    p_target['right_knee'] = p_target['right_pelvis'] + len_rig['right_pelvis2right_knee'] * uv_dict['right_pelvis2right_knee']
    p_target['right_ankle'] = p_target['right_knee'] + len_rig['right_knee2right_ankle'] * uv_dict['right_knee2right_ankle']
    p_target['left_pelvis'] = p[0,:] + len_rig['base2left_pelvis'] * uv_dict['base2left_pelvis']
    p_target['left_knee'] = p_target['left_pelvis'] + len_rig['left_pelvis2left_knee'] * uv_dict['left_pelvis2left_knee']
    p_target['left_ankle'] = p_target['left_knee'] + len_rig['left_knee2left_ankle'] * uv_dict['left_knee2left_ankle']
    p_target['spine1'] = p[0,:] + len_rig['base2spine'] * uv_dict['base2spine']
    p_target['right_shoulder'] = p_target['spine1'] + len_rig['spine2right_shoulder'] * uv_dict['spine2right_shoulder']
    p_target['right_elbow'] = p_target['right_shoulder'] + len_rig['right_shoulder2right_elbow'] * uv_dict['right_shoulder2right_elbow']
    p_target['right_wrist'] = p_target['right_elbow'] + len_rig['right_elbow2right_wrist'] * uv_dict['right_elbow2right_wrist']
    p_target['left_shoulder'] = p_target['spine1'] + len_rig['spine2left_shoulder'] * uv_dict['spine2left_shoulder']
    p_target['left_elbow'] = p_target['left_shoulder'] + len_rig['left_shoulder2left_elbow'] * uv_dict['left_shoulder2left_elbow']
    p_target['left_wrist'] = p_target['left_elbow'] + len_rig['left_elbow2left_wrist'] * uv_dict['left_elbow2left_wrist']
    
    return p_target

def finite_difference_matrix(n, dt, order):
    """
    n: number of points
    dt: time interval
    order: (1=velocity, 2=acceleration, 3=jerk)
    """ 
    # Order
    if order == 1:  # velocity
        coeffs = np.array([-1, 1])
    elif order == 2:  # acceleration
        coeffs = np.array([1, -2, 1])
    elif order == 3:  # jerk
        coeffs = np.array([-1, 3, -3, 1])
    else:
        raise ValueError("Order must be 1, 2, or 3.")

    # Fill-in matrix
    mat = np.zeros((n, n))
    for i in range(n - order):
        for j, c in enumerate(coeffs):
            mat[i, i + j] = c
    return mat / (dt ** order)

def get_A_vel_acc_jerk(n=100,dt=1e-2):
    """
        Get matrices to compute velocities, accelerations, and jerks
    """
    A_vel  = finite_difference_matrix(n,dt,order=1)
    A_acc  = finite_difference_matrix(n,dt,order=2)
    A_jerk = finite_difference_matrix(n,dt,order=3)
    return A_vel,A_acc,A_jerk

def slerp(q0, q1, t):
    qx, qy, qz, qw = 0, 1, 2, 3

    cos_half_theta = q0[..., qw] * q1[..., qw] \
                   + q0[..., qx] * q1[..., qx] \
                   + q0[..., qy] * q1[..., qy] \
                   + q0[..., qz] * q1[..., qz]
    
    neg_mask = cos_half_theta < 0
    q1 = q1.copy()
    q1[neg_mask] = -q1[neg_mask]
    cos_half_theta = np.abs(cos_half_theta)
    cos_half_theta = np.expand_dims(cos_half_theta, axis=-1)

    half_theta = np.arccos(cos_half_theta)
    sin_half_theta = np.sqrt(1.0 - cos_half_theta * cos_half_theta)

    ratioA = np.sin((1 - t) * half_theta) / sin_half_theta
    ratioB = np.sin(t * half_theta) / sin_half_theta; 
    
    new_q_x = ratioA * q0[..., qx:qx+1] + ratioB * q1[..., qx:qx+1]
    new_q_y = ratioA * q0[..., qy:qy+1] + ratioB * q1[..., qy:qy+1]
    new_q_z = ratioA * q0[..., qz:qz+1] + ratioB * q1[..., qz:qz+1]
    new_q_w = ratioA * q0[..., qw:qw+1] + ratioB * q1[..., qw:qw+1]

    cat_dim = len(new_q_w.shape) - 1
    new_q = np.concatenate([new_q_x, new_q_y, new_q_z, new_q_w], axis=cat_dim)

    new_q = np.where(np.abs(sin_half_theta) < 0.001, 0.5 * q0 + 0.5 * q1, new_q)
    new_q = np.where(np.abs(cos_half_theta) >= 1, q0, new_q)

    return new_q

def block_mtx(M11,M12,M21,M22):
    M_upper = np.concatenate((M11,M12),axis=1)
    M_lower = np.concatenate((M21,M22),axis=1)
    M = np.concatenate((M_upper,M_lower),axis=0)
    return M    

def det_inc(det_A,inv_A,b,c):
    """
        Incremental determinant computation
    """
    out = det_A * (c - b.T @ inv_A @ b)
    return out

def inv_inc(inv_A,b,c):
    """
        Incremental inverse using matrix inverse lemma
    """
    k   = c - b.T @ inv_A @ b
    M11 = inv_A + 1/k * inv_A @ b @ b.T @ inv_A
    M12 = -1/k * inv_A @ b
    M21 = -1/k * b.T @ inv_A
    M22 = 1/k
    M   = block_mtx(M11=M11,M12=M12,M21=M21,M22=M22)
    return M

def ikdpp(
    xs_total,              # [N x D]
    qs_total = None,       # [N]
    n_select = 10,
    n_trunc  = np.inf,
    hyp      = {'g':1.0,'l':1.0}
    ):
    """
        (Truncated) Incremental k-DPP
    """
    n_total     = xs_total.shape[0]
    idxs_remain = np.arange(0,n_total,1,dtype=np.int32)

    if n_total <= n_select: # in case of selecting more than what we already have
        xs_ikdpp   = xs_total
        idxs_ikdpp = idxs_remain
        return xs_ikdpp,idxs_ikdpp

    idxs_select = []
    for i_idx in range(n_select+1): # loop
        n_remain = len(idxs_remain)
        if i_idx == 0: # random first
            idx_select = np.random.permutation(n_total)[0]
            if qs_total is not None:
                q = 1.0+qs_total[idx_select]
            else:
                q = 1.0
            det_K_prev = q
            K_inv_prev = 1/q*np.ones(shape=(1,1))
        else:
            xs_select = xs_total[idxs_select,:]
            # Compute determinants
            dets = np.zeros(shape=n_remain)
            # for r_idx in range(n_remain): # for the remained indices
            for r_idx in np.random.permutation(n_remain)[:min(n_remain,n_trunc)]:
                # Compute the determinant of the appended kernel matrix 
                k_vec     = kernel_se(
                    X1  = xs_select,
                    X2  = xs_total[idxs_remain[r_idx],:].reshape(1,-1),
                    hyp = hyp)
                if qs_total is not None:
                    q = 1.0+qs_total[idxs_remain[r_idx]]
                else:
                    q = 1.0
                det_check = det_inc(
                    det_A = det_K_prev,
                    inv_A = K_inv_prev,
                    b     = k_vec,
                    c     = q)
                # Append the determinant
                dets[r_idx] = det_check
            # Get the index with the highest determinant
            idx_temp   = np.where(dets == np.amax(dets))[0][0]
            idx_select = idxs_remain[idx_temp]
            
            # Compute 'det_K_prev' and 'K_inv_prev'
            det_K_prev = dets[idx_temp]
            k_vec      = kernel_se(
                xs_select,
                xs_total[idx_select,:].reshape(1,-1),
                hyp = hyp)
            if qs_total is not None:
                q = 1+qs_total[idx_select]
            else:
                q = 1.0
            K_inv_prev = inv_inc(
                inv_A = K_inv_prev,
                b     = k_vec,
                c     = q)
        # Remove currently selected index from 'idxs_remain'
        idxs_remain = idxs_remain[idxs_remain != idx_select]
        # Append currently selected index to 'idxs_select'
        idxs_select.append(idx_select)
    # Select the subset from 'xs_total' with removing the first sample
    idxs_select = idxs_select[1:] # excluding the first one
    idxs_ikdpp  = np.array(idxs_select)
    xs_ikdpp    = xs_total[idxs_ikdpp]
    return xs_ikdpp,idxs_ikdpp

def np2torch(x_np,device='cpu'):
    """
        Numpy to Torch
    """
    if x_np is None:
        x_torch = None
    else:
        x_torch = torch.tensor(x_np,dtype=torch.float32,device=device)
    return x_torch

def torch2np(x_torch):
    """
        Torch to Numpy
    """
    if x_torch is None:
        x_np = None
    else:
        x_np = x_torch.detach().cpu().numpy()
    return x_np

def save_torch_wb(OBJ,folder_path='../weight',pth_name='wb.pth',VERBOSE=True):
    """
        Save torch weights and biases
    """
    os.makedirs(folder_path,exist_ok=True)
    pth_path = os.path.join(folder_path,pth_name)
    torch.save(obj=OBJ.state_dict(),f=pth_path)
    if VERBOSE:
        print ("[%s] saved."%(pth_path))

def load_torch_wb(OBJ,folder_path='../weight',pth_name='wb.pth',VERBOSE=True):
    """
        Load torch weights and biases
    """
    pth_path = os.path.join(folder_path,pth_name)
    OBJ.load_state_dict(torch.load(pth_path))
    if VERBOSE:
        print ("[%s] loaded."%(pth_path))