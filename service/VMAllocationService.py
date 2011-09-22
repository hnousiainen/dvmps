import threading
import time
import subprocess
import shutil
import os
import uuid
import random

class VMAllocationService():
    def __init__(self):
        self.allocated_images = {}
        self.mac_ip_records = {}
        self.configured_base_images = {}
        self.sync_lock = threading.RLock()

    def __create_image(self, image_id):
        self.sync_lock.acquire()
        if self.allocated_images.has_key(image_id):
            base_image = self.allocated_images[image_id]['base_image']
            full_path_image_file = self.allocated_images[image_id]['image_file_path']
            full_path_xml_def_file = self.allocated_images[image_id]['xml_def_path']
            mac = self.allocated_images[image_id]['mac']

            subprocess.call(['qemu-img', 'create', '-b', self.configured_base_images[base_image]['image_filename'], '-f', 'qcow2', full_path_image_file])

            f = open(self.configured_base_images[base_image]['template_filename'], 'r')
            xmlspec = f.read()
            f.close()
            xmlspec = xmlspec.replace('$(VM_ID)', image_id)
            xmlspec = xmlspec.replace('$(IMAGE_FILE)', full_path_image_file)
            xmlspec = xmlspec.replace('$(MAC_ADDRESS)', mac)
            f = open(full_path_xml_def_file, 'w')
            f.write(xmlspec)
            f.close()

        self.sync_lock.release()

    def __poweron_image(self, image_id):
        self.sync_lock.acquire()
        if self.allocated_images.has_key(image_id):
            full_path_xml_def_file = self.allocated_images[image_id]['xml_def_path']
            subprocess.call(['virsh', 'create', full_path_xml_def_file])
        self.sync_lock.release()

    def __poweroff_image(self, image_id):
        self.sync_lock.acquire()
        if self.allocated_images.has_key(image_id):
            subprocess.call(['virsh', 'destroy', image_id])
        self.sync_lock.release()

    def __destroy_image(self, image_id):
        self.sync_lock.acquire()
        if self.allocated_images.has_key(image_id):
            os.remove(self.allocated_images[image_id]['image_file_path'])
            os.remove(self.allocated_images[image_id]['xml_def_path'])
        self.sync_lock.release()

    def __cleanup_expired_images(self):
        self.sync_lock.acquire()
        image_ids = self.allocated_images.keys()
        for image_id in image_ids:
            image_record = self.allocated_images[image_id]
            time_before_expiry = image_record['creation_time'] + image_record['expires'] - int(time.time())
            if time_before_expiry < 0:
                self.__poweroff_image(image_id)
                self.__destroy_image(image_id)
                self.deallocate_mac(image_record['mac'])
                del self.allocated_images[image_id]
        self.sync_lock.release()

    def allocate_image(self, base_image, expires, comment):
        self.sync_lock.acquire()
        self.__cleanup_expired_images()

        if not self.configured_base_images.has_key(base_image):
            self.sync_lock.release()
            return { 'result': False, 'error': 'No such base image configured' }

        mac = self.allocate_mac()
        if mac is None:
            self.sync_lock.release()
            return { 'result': False, 'error': 'Could not allocate a free MAC address' }

        image_id = str(uuid.uuid4())
        ip_addr = self.find_ip_for_mac(mac)

        full_path_image_file = '/var/lib/libvirt/images/active_dynamic/%s.img' % image_id
        full_path_xml_def_file = '/var/lib/libvirt/qemu/active_dynamic/%s.xml' % image_id

        allocated_info = {}
        allocated_info['image_id'] = image_id
        allocated_info['mac'] = mac
        allocated_info['ip_addr'] = ip_addr
        allocated_info['base_image'] = base_image
        allocated_info['creation_time'] = int(time.time())
        allocated_info['expires'] = expires
        allocated_info['comment'] = ''
        if comment is not None:
            allocated_info['comment'] = comment
        allocated_info['image_file_path'] = full_path_image_file
        allocated_info['xml_def_path'] = full_path_xml_def_file

        self.allocated_images[image_id] = allocated_info

        self.__create_image(image_id)
        self.__poweron_image(image_id)

        ret_val = { 'result': True, 'image_id': image_id, 'ip_addr': ip_addr, 'base_image': base_image, 'expires': expires }        
        self.sync_lock.release()
        return ret_val

    def deallocate_image(self, image_id):
        self.sync_lock.acquire()
        self.__cleanup_expired_images()
        ret_val = { 'result': False, 'error': 'No such image' }

        if self.allocated_images.has_key(image_id):
            self.__poweroff_image(image_id)
            self.__destroy_image(image_id)
            self.deallocate_mac(self.allocated_images[image_id]['mac'])
            ret_val = { 'result': True, 'image_id': image_id, 'status': 'not-allocated' }
            del self.allocated_images[image_id]

        self.sync_lock.release()
        return ret_val

    def revert_image(self, image_id):
        self.sync_lock.acquire()
        self.__cleanup_expired_images()
        ret_val = { 'result': False, 'error': 'No such image' }

        if self.allocated_images.has_key(image_id):
            image_record = self.allocated_images[image_id]
            time_before_expiry = image_record['creation_time'] + image_record['expires'] - int(time.time())
            self.__poweroff_image(image_id)
            self.__destroy_image(image_id)
            self.__create_image(image_id)
            self.__poweron_image(image_id)
            ret_val = { 'result': True, 'image_id': image_id, 'status': 'allocated', 'ip_addr': image_record['ip_addr'], 'base_image': image_record['ip_addr'], 'expires': time_before_expiry, 'comment': image_record['comment'] }

        self.sync_lock.release()
        return ret_val

    def image_status(self, image_id):
        self.sync_lock.acquire()
        self.__cleanup_expired_images()
        ret_val = { 'result': True, 'image_id': image_id, 'status': 'not-allocated' }

        if self.allocated_images.has_key(image_id):
            image_record = self.allocated_images[image_id]
            time_before_expiry = image_record['creation_time'] + image_record['expires'] - int(time.time())
            ret_val = { 'result': True, 'image_id': image_id, 'status': 'allocated', 'ip_addr': image_record['ip_addr'], 'base_image': image_record['ip_addr'], 'expires': time_before_expiry, 'comment': image_record['comment'] }

        self.sync_lock.release()
        return ret_val

    def poweroff_image(self, image_id):
        self.sync_lock.acquire()
        self.__cleanup_expired_images()
        ret_val = { 'result': False, 'error': 'no such image' }

        if self.allocated_images.has_key(image_id):
            self.__poweroff_image(image_id)
            ret_val = { 'result': True, 'image_id': image_id }

        self.sync_lock.release()
        return ret_val

    def poweron_image(self, image_id):
        self.sync_lock.acquire()
        self.__cleanup_expired_images()
        ret_val = { 'result': False, 'error': 'no such image' }

        if self.allocated_images.has_key(image_id):
            self.__poweroon_image(image_id)
            ret_val = { 'result': True, 'image_id': image_id }

        self.sync_lock.release()
        return ret_val

    def status(self):
        self.sync_lock.acquire()
        self.__cleanup_expired_images()
        ret_val = { 'result': True, 'allocated_images': len(self.allocated_images) }
        self.sync_lock.release()
        return ret_val

    def define_mac_ip_pair(self, mac, ip):
        self.sync_lock.acquire()
        self.mac_ip_records[mac] = { 'ip_addr': ip, 'mac': mac, 'allocated': False }
        self.sync_lock.release()

    def allocate_mac(self):
        self.sync_lock.acquire()
        ret_val = None
        mac_keys = self.mac_ip_records.keys()
        random.shuffle(mac_keys)
        for key in mac_keys:
            if self.mac_ip_records[key]['allocated'] == False:
                self.mac_ip_records[key]['allocated'] = True
                ret_val = key
                break
        self.sync_lock.release()
        return ret_val

    def deallocate_mac(self, mac):
        self.sync_lock.acquire()
        if self.mac_ip_records.has_key(mac):
            self.mac_ip_records[mac]['allocated'] = False
        self.sync_lock.release()

    def find_ip_for_mac(self, mac):
        self.sync_lock.acquire()
        ret_val = None
        if self.mac_ip_records.has_key(mac):
            ret_val = self.mac_ip_records[mac]['ip_addr']
        self.sync_lock.release()
        return ret_val

    def define_base_image(self, base_id, template_filename, image_filename):
        self.sync_lock.acquire()
        self.configured_base_images[base_id] = { 'base_id': base_id, 'template_filename': template_filename, 'image_filename': image_filename }
        self.sync_lock.release()
