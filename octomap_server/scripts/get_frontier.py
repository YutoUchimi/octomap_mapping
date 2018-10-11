#!/usr/bin/env python

import time
import chainer
import chainer.functions as F
import copy
from geometry_msgs.msg import Point
import rospy
# from sensor_msgs.msg import PointCloud2
from visualization_msgs.msg import Marker
from visualization_msgs.msg import MarkerArray
from jsk_topic_tools import ConnectionBasedTransport
import numpy as np
# from std_srvs.srv import *


# when using this class, do not forget to subscribe topic which this class advertise
class FrontierPublisher(ConnectionBasedTransport):
    def __init__(self):
        super(FrontierPublisher, self).__init__()
        # rospy.wait_for_message(topic, topic_type, timeout)
        self.frontier_pub = self.advertise('frontier_cells_vis_array',
                                           MarkerArray, queue_size=1)
        # set occupancy region
        self.occupancy_region = {}
        self.min_x = '/octomap_server_contact/occupancy_min_x'
        self.max_x = '/octomap_server_contact/occupancy_max_x'
        self.min_y = '/octomap_server_contact/occupancy_min_y'
        self.max_y = '/octomap_server_contact/occupancy_max_y'
        self.min_z = '/octomap_server_contact/occupancy_min_z'
        self.max_z = '/octomap_server_contact/occupancy_max_z'
        for i, param in enumerate([self.min_x,
                                   self.max_x,
                                   self.min_y,
                                   self.max_y,
                                   self.min_z,
                                   self.max_z]):
            if rospy.has_param(param):
                self.occupancy_region[param] = rospy.get_param(param)
            else:
                # for bag, this is example
                self.occupancy_region[param] = [-0.3, 0.2, -0.6, -0.1, 0.2, 0.8][i]
        if rospy.has_param('/octomap_server_contact/resolution'):
            self.resolution = rospy.get_param('/octomap_server_contact/resolution')
        else:
            self.resolution = 0.005

        # bool array for occupied grid
        x_num = (self.occupancy_region[self.max_x]
                 - self.occupancy_region[self.min_x]) / self.resolution
        y_num = (self.occupancy_region[self.max_y]
                 - self.occupancy_region[self.min_y]) / self.resolution
        z_num = (self.occupancy_region[self.max_z]
                 - self.occupancy_region[self.min_z]) / self.resolution
        self.free = np.full((int(x_num), int(y_num), int(z_num)),
                            0,  # if it is free, this value is set 1
                            dtype=np.int)
        self.unknown = np.full((int(x_num), int(y_num), int(z_num)),
                               0,  # if it is unknown, this value is set 1
                               dtype=np.int)
        self.frontier = np.full((int(x_num), int(y_num), int(z_num)),
                                0,  # if it is frontier, this value is set 1
                                dtype=np.int)

    def subscribe(self):
        self.sub_free = rospy.Subscriber("free_cells_vis_array",
                                         MarkerArray, self.cb_free)
        self.sub_unknown = rospy.Subscriber("unknown_cells_vis_array",
                                            MarkerArray, self.cb_unknown)

    def unsubscribe(self):
        self.sub_free.unregister()
        self.sub_occupied.unregister()
        self.sub_unknown.unregister()

    def update_grid(self, marker_array, occupancy_type=True):
        x_min = self.occupancy_region[self.min_x]
        y_min = self.occupancy_region[self.min_y]
        z_min = self.occupancy_region[self.min_z]
        resolution = self.resolution

        if occupancy_type == 'free':
            self.free[:] = 0
            # update each grid
            # be careful that size of grids in octomap may differ from each other
            for marker in marker_array.markers:
                x_size = marker.scale.x
                y_size = marker.scale.y
                z_size = marker.scale.z
                x_num = int(x_size / resolution)
                y_num = int(y_size / resolution)
                z_num = int(z_size / resolution)
                for point in marker.points:
                    xyz_min = np.round(np.array([
                        point.x - (x_size / 2.0) - x_min,
                        point.y - (y_size / 2.0) - y_min,
                        point.z - (z_size / 2.0) - z_min]) / resolution).astype(np.int)
                    # NOTE +1 in below codes are very important to avoid making gap between unknown grids and frontier grids
                    self.free[xyz_min[0]:xyz_min[0]+x_num+1,
                              xyz_min[1]:xyz_min[1]+y_num+1,
                              xyz_min[2]:xyz_min[2]+z_num+1] = 1

        elif occupancy_type == 'unknown':
            self.unknown[:] = 0
            # be careful that size of all grids are resolution
            for marker in marker_array.markers:  # markers' length is always 1
                for point in marker.points:
                    xyz_min = np.round(np.array([
                        (point.x - x_min),
                        (point.y - y_min),
                        (point.z - z_min)]) / resolution - 1).astype(np.int)
                    self.unknown[xyz_min[0]][xyz_min[1]][xyz_min[2]] = 1

    # only when free grid topic comes, publish frontier grid
    def cb_free(self, msg):
        # rospy.loginfo('[cb_free]')
        self.update_grid(msg, 'free')

    def cb_unknown(self, msg):
        # rospy.loginfo('[cb_unknown] publish frontier grids')
        self.frame_id = msg.markers[0].header.frame_id
        self.ns = msg.markers[0].ns
        self.update_grid(msg, 'unknown')
        self.publish_frontier()

    def publish_frontier(self):
        self.frontier[:] = 0
        # use conv for detecting free grids adjacent to unknown grids
        unknown_grid = copy.copy(self.unknown)
        free_grid = copy.copy(self.free)
        unknown_grid = chainer.Variable(np.array([[self.unknown]], dtype=np.float32))
        max_grid = F.max_pooling_nd(unknown_grid, ksize=3, stride=1, pad=1).data[0][0]
        self.frontier = np.logical_and(
            max_grid == 1,
            free_grid == 1).astype(np.int)

        # for debug, visualize unknown grid as frontier grid
        # self.frontier = (self.unknown == 1).astype(np.int)
        # self.frontier = (self.free == 1).astype(np.int)
        # self.frontier = (max_grid == 1).astype(np.int)
        # print(11111111111111)
        # print(np.sum(self.frontier == 1))
        # print(np.sum(free_grid == 1))
        # print(np.sum(max_grid == 1))
        # print(22222222222222)

        pub_marker = MarkerArray()
        frontier_marker = Marker()
        point_list = [None] * np.sum(self.frontier == 1)
        count = 0
        for i in range(self.frontier.shape[0]):
            for j in range(self.frontier.shape[1]):
                for k in range(self.frontier.shape[2]):
                    if self.frontier[i][j][k]:
                        point = Point()
                        point.x = (i+1) * self.resolution + self.occupancy_region[self.min_x]
                        point.y = (j+1) * self.resolution + self.occupancy_region[self.min_y]
                        point.z = (k+1) * self.resolution + self.occupancy_region[self.min_z]
                        point_list[count] = point
                        count = count + 1

        frontier_marker.points = point_list
        frontier_marker.header.stamp = rospy.Time.now()
        frontier_marker.header.frame_id = self.frame_id
        frontier_marker.ns = self.ns
        frontier_marker.type = 6
        frontier_marker.action = 0
        frontier_marker.scale.x = self.resolution
        frontier_marker.scale.y = self.resolution
        frontier_marker.scale.z = self.resolution
        frontier_marker.color.r = 1.0
        frontier_marker.color.g = 0.0
        frontier_marker.color.b = 0.0
        frontier_marker.color.a = 1.0
        pub_marker.markers = [frontier_marker]

        self.frontier_pub.publish(pub_marker)


if __name__ == '__main__':
    rospy.init_node('get_frontier')
    rospy.loginfo("start publishing frontier grids")
    fp = FrontierPublisher()
    # s = rospy.Service('publish_frontier', Empty, fp.publish_frontier)
    rospy.spin()
