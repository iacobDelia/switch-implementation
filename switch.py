#!/usr/bin/python3
import sys
import struct
import wrapper
import threading
import time
from wrapper import recv_from_any_link, send_to_link, get_switch_mac, get_interface_name

def parse_ethernet_header(data):
    # Unpack the header fields from the byte array
    #dest_mac, src_mac, ethertype = struct.unpack('!6s6sH', data[:14])
    dest_mac = data[0:6]
    src_mac = data[6:12]
    
    # Extract ethertype. Under 802.1Q, this may be the bytes from the VLAN TAG
    ether_type = (data[12] << 8) + data[13]

    vlan_id = -1
    # Check for VLAN tag (0x8100 in network byte order is b'\x81\x00')
    if ether_type == 0x8200:
        vlan_tci = int.from_bytes(data[14:16], byteorder='big')
        vlan_id = vlan_tci & 0x0FFF  # extract the 12-bit VLAN ID
        ether_type = (data[16] << 8) + data[17]

    return dest_mac, src_mac, ether_type, vlan_id

def create_vlan_tag(vlan_id):
    # 0x8100 for the Ethertype for 802.1Q
    # vlan_id & 0x0FFF ensures that only the last 12 bits are used
    return struct.pack('!H', 0x8200) + struct.pack('!H', vlan_id & 0x0FFF)

def bpdu_init(vlan_list, priority):
    bridge = {}
    # set all trunk ports to blocked
    for port in vlan_list:
        if vlan_list[port] == 'T':
            bridge[port] = 'BLOCKED'
            root_port = port
    
    own_bridge_ID = priority
    root_bridge_ID = own_bridge_ID
    root_path_cost = 0

    if own_bridge_ID == root_bridge_ID:
        for port in bridge:
            bridge[port] = 'DESIGNATED_PORT'

    return bridge, own_bridge_ID, root_bridge_ID, root_path_cost, root_port

def send_bpdu(interface, own_bridge_id, path_cost, root_bridge_id):
    format = "!6s6sIII"
    dest_mac = b"\x01\x80\xC2\x00\x00\x00"
    bpdu = struct.pack(format, dest_mac, get_switch_mac(), own_bridge_id, path_cost, root_bridge_id)

    send_to_link(interface, len(bpdu), bpdu)
    

def send_bdpu_every_sec():
    while True:
        # if we are root send bpdu to all trunks
        if own_bridge_ID == root_bridge_ID:
            for port in vlan_list:
                if vlan_list[port] == 'T':
                    send_bpdu(port, own_bridge_ID, 0, root_bridge_ID)
        time.sleep(1)

def handle_bpdu(bridge, interface, received_root_bridge_ID, root_bridge_ID,
                own_bridge_ID, root_path_cost, sender_path_cost, sender_bridge_ID, root_port):
    # if we find a guy with higher priority, we update
    if received_root_bridge_ID < root_bridge_ID:
        # add 10 to cost
        root_path_cost += 10
        # if we were root, set all trunk interfaces to blocking except the root
        if root_bridge_ID == own_bridge_ID:
            for port in bridge:
                # leave root open
                if port == interface:
                    bridge[interface] = 'DESIGNATED PORT'
                else:
                    bridge[port] = 'BLOCKED'
        
        root_bridge_ID = received_root_bridge_ID
        root_port = interface
        # update all trunks with this new information
        for port in bridge:
            send_bpdu(port, own_bridge_ID, root_path_cost, root_bridge_ID)
    else:
        if received_root_bridge_ID == root_bridge_ID:
            if interface == root_port and sender_path_cost + 10 < root_path_cost:
                root_path_cost = sender_path_cost + 10
        else:
            if interface != root_port:
                if sender_path_cost > root_path_cost:
                    bridge[interface] = 'DESIGNATED PORT'
            else:
                if sender_bridge_ID == own_bridge_ID:
                    bridge[interface] = 'BLOCKED'
    
    if own_bridge_ID == root_bridge_ID:
        for port in bridge:
            bridge[port] = 'DESIGNATED PORT'

    return bridge, root_bridge_ID, root_path_cost, root_port


# read vlan info from config files
def read_info(switch_id, interfaces):
    line_number = 0;
    interface_vlan_list = {}
    with open('configs/switch' + switch_id + ".cfg", "r") as file:
        for line in file:
            split_line = line.split()
            # first line, read the priority
            if(line_number == 0):
                priority = int(split_line[0])
            else:
                for i in interfaces:
                    if get_interface_name(i) == split_line[0]:
                        interface_vlan_list[i] = split_line[1]
            line_number += 1
    
    return priority, interface_vlan_list
 
def is_unicast(mac):
    return int(mac.split(":")[0], 16) % 2 == 0

def forward_frame_with_vlan(interf_source, interf_dest, data, vlan_list, length, vlan_id):
    if vlan_list[int(interf_dest)] == 'T':
        if bridge[interf_dest] == 'BLOCKED':
            return;

    if(vlan_id == -1):
        vlan_id = int(vlan_list[interf_source])

    data_with_tag = data[0:12] + create_vlan_tag(vlan_id) + data[12:]
    data_without_tag = data[0:12] + data[16:]

    # check if the interface we are sending it on is trunk
    if(vlan_list[interf_dest] == 'T'):
        # now check if it came from a trunk interface
        if(vlan_list[interf_source] == 'T'):
        # if it did, we send it as is, because it already has the 802 tag
            send_to_link(interf_dest, length, data)
        else: # if not, we add it ourselves
            send_to_link(interf_dest, length + 4, data_with_tag)
    else: # the interface we are sending it on is access
        # check if the vlan ids match
        if(vlan_id == int(vlan_list[interf_dest])):
            # also check if it came from a trunk interface
            if(vlan_list[int(interf_source)] == 'T'):
                # if it did, remove the tag
                send_to_link(interf_dest, length - 4, data_without_tag)
            else:
                send_to_link(interf_dest, length, data)


def main():
    # init returns the max interface number. Our interfaces
    # are 0, 1, 2, ..., init_ret value + 1
    switch_id = sys.argv[1]

    num_interfaces = wrapper.init(sys.argv[2:])
    interfaces = range(0, num_interfaces)

    print("# Starting switch with id {}".format(switch_id), flush=True)
    print("[INFO] Switch MAC", ':'.join(f'{b:02x}' for b in get_switch_mac()))

    # initially, these werent global, but i needed them to be for the send_bpu_every_sec function
    global bridge
    global own_bridge_ID
    global vlan_list
    global root_bridge_ID
    global root_path_cost
    global src_mac
    global root_port

    # init our switch
    MAC_table = {}
    priority, vlan_list = read_info(switch_id, interfaces)
    bridge, own_bridge_ID, root_bridge_ID, root_path_cost, root_port = bpdu_init(vlan_list, priority)

    format = "!6s6sIII"
    # Create and start a new thread that deals with sending BDPU
    t = threading.Thread(target=send_bdpu_every_sec)
    t.start()

    # Printing interface names
    for i in interfaces:
        print(get_interface_name(i))

    while True:
        # Note that data is of type bytes([...]).
        # b1 = bytes([72, 101, 108, 108, 111])  # "Hello"
        # b2 = bytes([32, 87, 111, 114, 108, 100])  # " World"
        # b3 = b1[0:2] + b[3:4].
        interface, data, length = recv_from_any_link()

        dest_mac, src_mac, ethertype, vlan_id = parse_ethernet_header(data)

        # Print the MAC src and MAC dst in human readable format
        dest_mac = ':'.join(f'{b:02x}' for b in dest_mac)
        src_mac = ':'.join(f'{b:02x}' for b in src_mac)

        # Note. Adding a VLAN tag can be as easy as
        # tagged_frame = data[0:12] + create_vlan_tag(10) + data[12:]

        print(f'Destination MAC: {dest_mac}')
        print(f'Source MAC: {src_mac}')
        print(f'EtherType: {ethertype}')
        
        print("Received frame of size {} on interface {}".format(length, interface), flush=True)

        MAC_table[src_mac] = interface
        if dest_mac == "01:80:c2:00:00:00":
            dest_mac, src_mac, sender_bridge_id, sender_path_cost, received_root_bridge_ID = struct.unpack(format, data)
            # i know this doesn't look pretty but if it works, it works..
            bridge, root_bridge_ID, root_path_cost, root_port = handle_bpdu(bridge, interface, received_root_bridge_ID, 
                                                        root_bridge_ID, own_bridge_ID, root_path_cost, sender_path_cost,
                                                        sender_bridge_id, root_port)
        else:
            if is_unicast(dest_mac):
                if dest_mac in MAC_table:
                    forward_frame_with_vlan(interface, MAC_table[dest_mac], data, vlan_list,
                                            length, vlan_id)
                else:
                    for o in interfaces:
                        if interface != int(o):                            
                            forward_frame_with_vlan(interface, o, data, vlan_list,
                                            length, vlan_id)
            else:
                for o in interfaces:
                    if interface != int(o):
                        forward_frame_with_vlan(interface, o, data, vlan_list,
                                            length, vlan_id)

if __name__ == "__main__":
    main()
