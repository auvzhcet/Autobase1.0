#!/usr/bin/env python

import time
import numpy as np
import matplotlib.pyplot as plt

import rospy
#import sys
#sys.path.append(str(pathlib.Path(__file__).parent.parent.parent))
import cubic_spline_planner

from nav_msgs.msg import Path, Odometry
from geometry_msgs.msg import Twist
from tf.transformations import euler_from_quaternion
import tf
from collections import deque

from communication.msg import CarState

class State():
    """
    Class representing the state of a vehicle.

    :param x: (float) x-coordinate
    :param y: (float) y-coordinate
    :param yaw: (float) yaw angle
    :param v: (float) speed
    """

    def __init__(self, x=0.0, y=0.0, yaw=0.0, v=0.0):
        """Instantiate the object."""
        super(State, self).__init__()
        self.x = x
        self.y = y
        self.yaw = yaw  # from IMU
        self.v = v

class Stanley:
    def __init__(self, k, dt, L, max_steer):
        self.k = k  # control gain
        self.dt = dt  # [s] time difference
        self.L = L  # [m] Wheel base of vehicle
        self.max_steer = np.radians(max_steer)  # [rad] max steering angle
        
        self.target_speed = 7# km/h
        self.target_speed_history = deque(maxlen=10)

        self.waypoints_list = []
        self.waypoints_type = "sparse" # ["dense", "sparse"]

        self.cx = []
        self.cy = []
        self.cyaw = []

        self.state = None
        self.tf_listener = tf.TransformListener()

    def stanley_control(self, state, cx, cy, cyaw, last_target_idx):
        """
        Stanley steering control.

        :param state: (State object)
        :param cx: ([float])
        :param cy: ([float])
        :param cyaw: ([float])
        :param last_target_idx: (int)
        :return: (float, int)
        """
        current_target_idx, error_front_axle = self.calc_target_index(state, cx, cy)

        # if last_target_idx >= current_target_idx:
        #     current_target_idx = last_target_idx

        # theta_e corrects the heading error
        #print(f"I am cyaw[current_target_idx] {cyaw[current_target_idx]}")
        #print(f"I am state.yaw {state.yaw}")
        theta_e = self.normalize_angle(cyaw[current_target_idx] - state.yaw)

        #print(f"I am theta e {-theta_e}")
        
        # theta_d corrects the cross track error
        # theta_d = np.arctan2(self.k * error_front_axle, state.v)
        #print(f"I am self.k {self.k}")
        #print(f"I am error_front_axle {error_front_axle}")
        #print(f"I am state.v {state.v}")
        theta_d = np.arctan2(self.k * error_front_axle, state.v)
       # print(f"I am theta d {theta_d}")
        if abs(state.v) < 0.01:
            theta_d = 0

        # Steering control
        delta = theta_e + theta_d
        return delta, current_target_idx


    def normalize_angle(self, angle):
        """
        Normalize an angle to [-pi, pi].

        :param angle: (float)
        :return: (float) Angle in radian in [-pi, pi]
        """
        while angle > np.pi:
            angle -= 2.0 * np.pi

        while angle < -np.pi:
            angle += 2.0 * np.pi

        return angle


    def calc_target_index(self, state, cx, cy):
        """
        Compute index in the trajectory list of the target.

        :param state: (State object)
        :param cx: [float]
        :param cy: [float]
        :return: (int, float)
        """
        # Calc front axle position
        fy = state.y + (self.L * np.sin(state.yaw))
        fx = state.x + (self.L * np.cos(state.yaw))

        # Search nearest point index
        dx = [fx - i_cx for i_cx in cx]
        dy = [fy - i_cy for i_cy in cy]
        d = np.hypot(dx, dy)
        target_idx = np.argmin(d)

        # TODO : maths behind calculating cross track error??
        # Project RMS error onto front axle vector
        front_axle_vec = [-np.cos(state.yaw + np.pi / 2),
                        -np.sin(state.yaw + np.pi / 2)]
        error_front_axle = np.dot([dx[target_idx], dy[target_idx]], front_axle_vec)

        return target_idx, error_front_axle

    def init_subscribers(self):
        rospy.loginfo("subscribing to topics")
        rospy.Subscriber("/ll_state", CarState, callback=self.velocity_callback, queue_size=1)
        rospy.Subscriber("/tf/odometry", Odometry, callback=self.odometry_callback, queue_size=1)
        rospy.Subscriber("/odom", Odometry, callback=self.odometry_callback, queue_size=1)
        
        rospy.Subscriber("/local_planner/waypoints", Path, callback=self.waypoints_callback, queue_size=1, buff_size=2**24)
        rospy.Subscriber("/local_planner/info", Twist, callback=self.local_planner_info_callback, queue_size=1, buff_size=2**24)
        self.tf_listener = tf.TransformListener()

    def init_pulishers(self):
        rospy.loginfo("creating publishers")
        self.controlCommandPub = rospy.Publisher("/cmd_vel", Twist, queue_size=1)

    def velocity_callback(self, ll_state):
        # print("ll_state received")
        if self.state != None:
            velocity = ll_state.velocity # km/h 
            self.state.v = velocity / 3.6 # m/s

    def odometry_callback(self, data):
        #print("odometry received")
        try:
            (trans, rot) =self.tf_listener.lookupTransform('map', 'base_link', rospy.Time(0))
        except Exception as e:
            rospy.loginfo("Exception - %s", e)
            return

        if self.state == None:
            self.state = State()
        # self.state.x = odom.pose.pose.position.x
        # self.state.y = odom.pose.pose.position.y
        # self.state.yaw = euler_from_quaternion([odom.pose.pose.orientation.x,
        #                      odom.pose.pose.orientation.y, odom.pose.pose.orientation.z,
        #                      odom.pose.pose.orientation.w])[2]

        self.state.x = trans[0]
        self.state.y = trans[1]
        self.state.yaw = euler_from_quaternion(rot)[2]

        
    def waypoints_callback(self, waypoints):
        self.waypoints_list = waypoints.poses
        # print("waypoints received")
        if self.waypoints_type == "dense":        
            self.cx = []
            self.cy = []
            self.cyaw = []
            for wpt in self.waypoints_list:
                self.cx.append(wpt.pose.position.x)
                self.cy.append(wpt.pose.position.y)
                self.cyaw.append(wpt.pose.orientation.z)
        elif self.waypoints_type == "sparse":
            
            ax = []
            ay = [] 

            for wpt in self.waypoints_list:
                ax.append(wpt.pose.position.x)
                ay.append(wpt.pose.position.y)
            
            ds = 0.1  # [m] distance of each interpolated points
            cx, cy, cyaw, ck, s = cubic_spline_planner.calc_spline_course(ax, ay, ds)
           
            
            self.cx = cx
            self.cy = cy
            self.cyaw = cyaw
            #print("I am cx")
            #print(self.cx)
            #print("I am cy")
            #print(self.cy)
            #print("I am cyaw")
            #print(self.cyaw)
            #if self.show_animation:  # pragma: no cover    
                #plt.plot(cx, cy, ".r", label="course")
                #plt.plot(ax, ay, "-b", label="trajectory")
                #plt.plot(cx, cy, "xg", label="target")
                #plt.axis("equal")
               # plt.grid(True)
               # plt.title("Speed[km/h]:" + str(self.state.v * 3.6)[:4])
               # plt.pause(0.001)
                
                #t = np.arange(0, len(ax) * ds, ds)
                #plt.plot(t, [iv * 3.6 for iv in self.target_speed_history], "-r")
                #plt.xlabel("Time[s]")
                #plt.ylabel("Speed[km/h]")
                #plt.grid(True)
                #plt.show()
        self.totalWaypoints = len(self.cx)
        rospy.loginfo("total waypoints %d", self.totalWaypoints)

    def local_planner_info_callback(self, info):
        speed = info.linear.x
        self.path_curvature = info.angular.z

        self.target_speed_history.append(speed)
        self.target_speed = np.floor(np.mean(np.array(self.target_speed_history)))

    def call_controller(self):
        last_idx = len(self.cx) - 1
        # print(self.cx)
        if (len(self.cx) == 0):
            return

        control_command = Twist()
        target_idx, _ = self.calc_target_index(self.state, self.cx, self.cy)
        # print(last_idx, target_idx)
        if last_idx > target_idx:
            steer, target_idx = self.stanley_control(self.state, self.cx, self.cy, self.cyaw, target_idx)

            steer = np.clip(steer, -self.max_steer, self.max_steer) # radians
            steer = np.degrees(steer) # converting to degrees

            control_command.angular.z = steer
            control_command.linear.x = self.target_speed
            # print("Current Pose : {:.2f}, {:.2f}, {:.2f}; Target Point : {:.2f}, {:.2f}, {:.2f}; steer : {:.2f}".format(self.state.x, self.state.y, self.state.yaw, self.cx[target_idx], self.cy[target_idx], self.cyaw[target_idx], steer))
            # print("Target Point : {:.2f}, {:.2f}".format(self.cx[target_idx], self.cy[target_idx]))
            # print("Vel : {} ; Steer : {}".format(self.target_speed, steer))
            ret_val = 0
        else:
            rospy.loginfo("Reached Target. Stop car!!")
            control_command.angular.z = 0
            control_command.linear.x = 0
            ret_val = -1
        self.controlCommandPub.publish(control_command)
        return ret_val

def main():
    rospy.init_node('stanley_controller')

    # k = rospy.get_param('/stanley_controller/k') | This will work
    # k = rospy.get_param('k') | This will also work
    params = rospy.get_param(rospy.get_name())
    
    controller = Stanley(k=params['k'], dt=params['dt'], L=params['L'], max_steer=params['max_steer'])
    controller.init_subscribers()
    controller.init_pulishers()
    
    rate = rospy.Rate(100)

    err_log = False
    while not rospy.is_shutdown():
        # print("in loop")
        if (controller.state == None) or (len(controller.waypoints_list) == 0):
            if (controller.state == None):
                if not err_log:
                    rospy.logwarn("odometry not initiated")
            elif (len(controller.waypoints_list) == 0):
                if not err_log:
                    rospy.logwarn("waypoints list is empty")
            err_log = True
            # print("continue")
            continue
        err_log = False

        ret_val = controller.call_controller()
        if ret_val == -1:
            controller.waypoints_list = []
            
        time.sleep(0.1)        

if __name__ == u'__main__':
    try:
        main()
    except rospy.ROSInterruptException:
        pass
