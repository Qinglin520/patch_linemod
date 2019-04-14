import os
import sys
import time
import numpy as np
import cv2
import math
from pysixd import view_sampler, inout, misc
from params.dataset_params import get_dataset_params
from os.path import join
import linemodLevelup_pybind
import pose_refine_pybind

from pysixd import renderer

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def draw_axis(img, R, t, K):
    # unit is mm
    rotV, _ = cv2.Rodrigues(R)
    points = np.float32([[100, 0, 0], [0, 100, 0], [0, 0, 100], [0, 0, 0]]).reshape(-1, 3)
    axisPoints, _ = cv2.projectPoints(points, rotV, t, K, (0, 0, 0, 0))
    img = cv2.line(img, tuple(axisPoints[3].ravel()), tuple(axisPoints[0].ravel()), (255,0,0), 3)
    img = cv2.line(img, tuple(axisPoints[3].ravel()), tuple(axisPoints[1].ravel()), (0,255,0), 3)
    img = cv2.line(img, tuple(axisPoints[3].ravel()), tuple(axisPoints[2].ravel()), (0,0,255), 3)
    return img

dataset = 'hinterstoisser'
# dataset = 'tless'
# dataset = 'tudlight'
# dataset = 'rutgers'
# dataset = 'tejani'
# dataset = 'doumanoglou'
# dataset = 'toyotalight'

# mode = 'render_train'
mode = 'test'

dp = get_dataset_params(dataset)
detector = linemodLevelup_pybind.Detector(16, [4, 8], 16)  # min features; pyramid strides; num clusters

obj_ids = []  # for each obj
obj_ids_curr = range(1, dp['obj_count'] + 1)
if obj_ids:
    obj_ids_curr = set(obj_ids_curr).intersection(obj_ids)

scene_ids = []  # for each obj
im_ids = []  # obj's img
gt_ids = []  # multi obj in one img
scene_ids_curr = range(1, dp['scene_count'] + 1)
if scene_ids:
    scene_ids_curr = set(scene_ids_curr).intersection(scene_ids)

# mm
dep_range = 200  # max depth range of objects
dep_anchors = []  # depth to apply templates

dep_min = dp['test_obj_depth_range'][0]  # min depth of scene
dep_max = dp['test_obj_depth_range'][1]  # max depth of scene
dep_anchor_step = 1.2  # depth scale

# dep_min = 400  # min depth of scene
# dep_max = 1000  # max depth of scene
# dep_anchor_step = 1.2  # depth scale

current_dep = dep_min
while current_dep < dep_max:
    dep_anchors.append(int(current_dep))
    current_dep = current_dep*dep_anchor_step

# dep_anchors = dep_anchors[1:-1]  # discard two border dep

print('\ndep anchors:\n {}, \ndep range: {}\n'.format(dep_anchors, dep_range))

top_level_path = os.path.dirname(os.path.abspath(__file__))
template_saved_to = join(dp['base_path'], 'linemod_render_up', '%s.yaml')
tempInfo_saved_to = join(dp['base_path'], 'linemod_render_up', '{:02d}_info_{}.yaml')
result_base_path = join(top_level_path, 'public', 'sixd_results', 'patch-linemod_'+dataset)

misc.ensure_dir(os.path.dirname(template_saved_to))
misc.ensure_dir(os.path.dirname(tempInfo_saved_to))
misc.ensure_dir(result_base_path)

if mode == 'render_train':
    start_time = time.time()

    im_size = dp['cam']['im_size']
    shape = (im_size[1], im_size[0])

    for obj_id in obj_ids_curr:
        azimuth_range = dp['test_obj_azimuth_range']
        elev_range = dp['test_obj_elev_range']
        min_n_views = 200

        model_path = dp['model_mpath'].format(obj_id)
        # width height model_path
        pose_refiner = pose_refine_pybind.PoseRefine(model_path)
        pose_refiner.set_K_width_height(dp['cam']['K'].astype(np.float32), im_size[0], im_size[1])

        for radius in dep_anchors:
            # with camera tilt
            # tilt_factor = (80 / 180)
            tilt_factor = 1
            views, views_level = view_sampler.sample_views(min_n_views, radius,
                                                           azimuth_range, elev_range,
                                                           tilt_range=(-math.pi * tilt_factor,
                                                                       math.pi * tilt_factor),
                                                           tilt_step=math.pi / 10)
            print('Sampled views: ' + str(len(views)))
            templateInfo = dict()

            # Render the object model from all the views
            for view_id, view in enumerate(views):
                if view_id % 50 == 0:
                    print(dataset + ' obj,radius,view: ' + str(obj_id) +
                          ',' + str(radius) + ',' + str(view_id) + ', view_id: ', view_id)

                mat_view = np.eye(4, dtype=np.float32)
                mat_view[:3, :3] = view['R']
                mat_view[:3, 3] = view['t'].squeeze()

                [[depth, mask]] = pose_refiner.render_depth_mask([mat_view.astype(np.float32)])

                visual = True
                if visual:
                    cv2.imshow('mask', mask)
                    cv2.waitKey(1)

                if dp['cam']['depth_scale'] != 1:
                    depth *= dp['cam']['depth_scale']
                    depth = depth.astype(np.uint16)

                K = dp['cam']['K']
                R = view['R']
                t = view['t']
                # have read rgb, depth, pose, obj_bb, obj_id here

                rows = np.any(depth, axis=1)
                cols = np.any(depth, axis=0)
                ymin, ymax = np.where(rows)[0][[0, -1]]
                xmin, xmax = np.where(cols)[0][[0, -1]]

                aTemplateInfo = dict()
                aTemplateInfo['cam_K'] = K
                aTemplateInfo['cam_R_w2c'] = R
                aTemplateInfo['cam_t_w2c'] = t
                aTemplateInfo['width'] = int(xmax - xmin)
                aTemplateInfo['height'] = int(ymax - ymin)

                # well, mask can replace rgb, because we only care about silhouette
                # (I won't say I'm too lazy to write a rgb renderer)
                success = detector.addTemplate([mask, depth], '{:02d}_template_{}'.format(obj_id, radius))
                print('success {}'.format(success[0]))

                if success[0] != -1:
                    templateInfo[success[0]] = aTemplateInfo

            inout.save_info(tempInfo_saved_to.format(obj_id, radius), templateInfo)
            detector.writeClasses(template_saved_to)
            #  clear to save RAM
            detector.clear_classes()

    elapsed_time = time.time() - start_time
    print('train time: {}\n'.format(elapsed_time))

if mode == 'test':
    im_size = dp['test_im_size']
    shape = (im_size[1], im_size[0])
    print('test img size: {}'.format(shape))

    use_image_subset = True
    if use_image_subset:
        im_ids_sets = inout.load_yaml(dp['test_set_fpath'])
    else:
        im_ids_sets = None

    for scene_id in scene_ids_curr:
        obj_id_in_scene_array = list()
        obj_id_in_scene_array.append(scene_id)

        if dataset =='doumanoglou' and scene_id == 3:
            obj_id_in_scene_array = [1, 2]

        if dataset == 'hinterstoisser' and scene_id == 2:
            obj_id_in_scene_array = [1, 2, 5, 6, 8, 9, 10, 11, 12]  # for occ dataset

        for obj_id_in_scene in obj_id_in_scene_array:
            # Load scene info and gt poses
            print('#' * 20)
            print('\nreading detector template & info, obj: {}'.format(obj_id_in_scene))
            misc.ensure_dir(join(result_base_path, '{:02d}'.format(scene_id)))
            scene_info = inout.load_info(dp['scene_info_mpath'].format(scene_id))
            scene_gt = inout.load_gt(dp['scene_gt_mpath'].format(scene_id))

            pose_refiner = pose_refine_pybind.PoseRefine(dp['model_mpath'].format(obj_id_in_scene))

            template_read_classes = []
            detector.clear_classes()
            for radius in dep_anchors:
                template_read_classes.append('{:02d}_template_{}'.format(obj_id_in_scene, radius))
            detector.readClasses(template_read_classes, template_saved_to)

            print('num templs: {}'.format(detector.numTemplates()))

            templateInfo = dict()
            for radius in dep_anchors:
                key = tempInfo_saved_to.format(obj_id_in_scene, radius)
                aTemplateInfo = inout.load_info(key)
                key = os.path.basename(key)
                key = os.path.splitext(key)[0]
                key = key.replace('info', 'template')
                templateInfo[key] = aTemplateInfo

            # Considered subset of images for the current scene
            if im_ids_sets is not None:
                im_ids_curr = im_ids_sets[scene_id]
            else:
                im_ids_curr = sorted(scene_info.keys())

            if im_ids:
                im_ids_curr = set(im_ids_curr).intersection(im_ids)

            active_ratio = 0.7
            for im_id in im_ids_curr:

                start_time = time.time()

                print('#' * 20)
                print('scene: {}, im: {}'.format(scene_id, im_id))

                K = scene_info[im_id]['cam_K']
                pose_refiner.set_K(K.astype(np.float32))

                # Load the images
                rgb = inout.load_im(dp['test_rgb_mpath'].format(scene_id, im_id))
                depth = inout.load_depth(dp['test_depth_mpath'].format(scene_id, im_id))
                depth *= dp['cam']['depth_scale']
                depth = depth.astype(np.uint16)  # [mm]
                im_size = (depth.shape[1], depth.shape[0])

                pose_refiner.set_depth(depth)

                match_ids = list()

                for radius in dep_anchors:
                    match_ids.append('{:02d}_template_{}'.format(obj_id_in_scene, radius))

                # srcs, score for one part, active ratio, may be too low for simple objects so too many candidates?
                matches = detector.match([rgb, depth], 70, active_ratio,
                                         match_ids, dep_anchors, dep_range, masks=[])

                if len(matches) > 0:
                    aTemplateInfo = templateInfo[matches[0].class_id]
                    render_K = aTemplateInfo[0]['cam_K']

                print('candidates size before refine & nms: {}\n'.format(len(matches)))

                local_refine_start = time.time()

                top100_local_refine = 100  # avoid too many for simple obj,
                # we observed more than 1000 when active ratio too low

                if top100_local_refine > len(matches):
                    top100_local_refine = len(matches)

                raw_match_rgb = np.copy(rgb)

                matched_poses = []
                for i in range(top100_local_refine):
                    match = matches[i]
                    templ = detector.getTemplates(match.class_id, match.template_id)
                    cv2.circle(raw_match_rgb, (int(match.x + templ[0].width / 2), int(match.y + templ[0].height / 2)),
                               2, (0, 0, 255), -1)

                    aTemplateInfo = templateInfo[match.class_id]
                    R_match = aTemplateInfo[match.template_id]['cam_R_w2c']
                    t_match = aTemplateInfo[match.template_id]['cam_t_w2c']
                    mat_view = np.eye(4, dtype=np.float32)
                    mat_view[:3, :3] = R_match
                    mat_view[:3, 3] = t_match.squeeze()
                    matched_poses.append(mat_view.astype(np.float32))

                results_refined = []
                if len(matches) > 0:
                    init_poses = linemodLevelup_pybind.matches2poses(matches, detector, matched_poses,
                                                                     K.astype(np.float32),
                                                                     top100_local_refine)
                    poses_extended = pose_refiner.poses_extend(init_poses)
                    results_unfiltered = pose_refiner.process_batch(init_poses)

                    # edge hit rate, active ratio, rmse
                    results_refined = pose_refiner.results_filter(results_unfiltered, active_ratio, active_ratio)

                print('candidates size after refine & nms: {}\n'.format(len(results_refined)))

                top10 = 10
                if top10 > len(results_refined):
                    top10 = len(results_refined)

                result = {}
                result_ests = []
                result_name = join(result_base_path, '{:02d}'.format(scene_id),
                                   '{:04d}_{:02d}.yml'.format(im_id, obj_id_in_scene))

                for i in range(top10):
                    e = dict()

                    resultT = np.array(results_refined[i].transformation_)
                    e['R'] = resultT[0:3, 0:3]
                    e['t'] = resultT[:3, 3].squeeze()
                    e['score'] = 1/(results_refined[i].inlier_rmse_ + 0.01)
                    result_ests.append(e)

                print('local refine time: {}s'.format(time.time() - local_refine_start))

                matching_time = time.time() - start_time
                print('total time: {}s\n'.format(matching_time))

                result['ests'] = result_ests
                inout.save_results_sixd17(result_name, result, matching_time)

                scores = []
                for e in result_ests:
                    scores.append(e['score'])
                sort_index = np.argsort(np.array(scores))  # ascending

                # draw results
                render_rgb = np.copy(rgb)
                for i in range(len(scores)):
                    render_R = result_ests[sort_index[i]]['R']
                    render_t = result_ests[sort_index[i]]['t']

                    mat_view = np.eye(4, dtype=np.float32)
                    mat_view[:3, :3] = render_R
                    mat_view[:3, 3] = render_t.squeeze()
                    [render_depth] = pose_refiner.render_depth([mat_view.astype(np.float32)])
                    render_rgb_new = pose_refiner.view_dep(render_depth)
                    visible_mask = render_depth < depth
                    mask = render_depth > 0
                    mask = mask.astype(np.uint8)
                    rgb_mask = np.dstack([mask] * 3)
                    render_rgb = render_rgb * (1 - rgb_mask) + render_rgb_new * rgb_mask

                    draw_axis(render_rgb, render_R, render_t, K)

                    if i == len(scores) - 1:  # best result
                        draw_axis(rgb, render_R, render_t, K)

                visual = True
                # visual = False
                if visual:
                    cv2.imshow('raw', raw_match_rgb)
                    cv2.imshow('depth_edge', pose_refiner.scene_dep_edge)
                    cv2.imshow('rgb_top1', rgb)
                    cv2.imshow('rgb_render', render_rgb)
                    cv2.waitKey(1000)
print('end line')
