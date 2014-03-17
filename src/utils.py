#/usr/bin/env python

import os
import time

import numpy as np
import matplotlib.pyplot as plt
import pandas
import yaml
from gaitanalysis import motek
from gaitanalysis.gait import WalkingData
from gaitanalysis.controlid import SimpleControlSolver
from gaitanalysis.utils import _percent_formatter
from dtk.process import coefficient_of_determination


def trial_file_paths(trials_dir, trial_number):
    """Returns the most comman paths to the trials in the gait
    identification data set.

    Parameters
    ==========
    trials_dir : string
        The path to the main directory for the data. This directory should
        contain subdirectories: `T001/`, `T002/`, etc.
    trial_number : string
        Three digit trial number, e.g. `005`.

    """

    trial_dir = 'T' + trial_number
    mocap_file = 'mocap-' + trial_number + '.txt'
    record_file = 'record-' + trial_number + '.txt'
    meta_file = 'meta-' + trial_number + '.yml'

    mocap_file_path = os.path.join(trials_dir, trial_dir, mocap_file)
    record_file_path = os.path.join(trials_dir, trial_dir, record_file)
    meta_file_path = os.path.join(trials_dir, trial_dir, meta_file)

    return mocap_file_path, record_file_path, meta_file_path


def add_negative_columns(data, axis, inv_dyn_labels):
    """Creates new columns in the DataFrame for any D-Flow measurements in
    the Z axis.

    Parameters
    ==========
    data : pandas.DataFrame
    axis : string
        A string that is uniquely in all columns you want to make a negative
        copy of, typically 'X', 'Y', or 'Z'.

    Returns
    =======
    new_inv_dyn_labels : list of strings
        New column labels.

    """

    new_inv_dyn_labels = []
    for label_set in inv_dyn_labels:
        new_label_set = []
        for label in label_set:
            if axis in label:
                new_label = 'Negative' + label
                data[new_label] = -data[label]
            else:
                new_label = label
            new_label_set.append(new_label)
        new_inv_dyn_labels.append(new_label_set)

    return new_inv_dyn_labels


def tmp_data_dir(default='data'):
    """Returns a valid temporary data directory."""

    # If there is a config file in the current directory, then load it, else
    # set the default data directory to current directory.
    try:
        f = open('config.yml')
    except IOError:
        tmp_dir = default
    else:
        config_dict = yaml.load(f)
        tmp_dir = config_dict['tmp_data_directory']
        f.close()

    if not os.path.isdir(tmp_dir):
        os.makedirs(tmp_dir)

    print('Temporary data directory is set to {}'.format(tmp_dir))

    return tmp_dir


def trial_data_dir(default='.'):
    """Returns the trials directory."""

    # If there is a config file in the current directory, then load it, else
    # set the default data directory to current directory.
    try:
        f = open('config.yml')
    except IOError:
        trials_dir = default
    else:
        config_dict = yaml.load(f)
        trials_dir = config_dict['root_data_directory']
        f.close()

    print('Trials data directory is set to {}'.format(trials_dir))

    return trials_dir


def generate_meta_data_table(trials_dir):

    trial_dirs = [x[0] for x in os.walk(trials_dir)]

    keys_i_want = ['id', 'subject-id', 'datetime', 'notes', 'nominal-speed']

    data = {}
    for k in keys_i_want:
        data.setdefault(k, [])

    for directory in trial_dirs:
        try:
            f = open(os.path.join(directory,
                                  'meta-{}.yml'.format(directory[-3:])))
        except IOError:
            pass
        else:
            meta_data = yaml.load(f)
            trial_dic = meta_data['trial']

            for key in keys_i_want:
                try:
                    data[key].append(trial_dic[key])
                except KeyError:
                    data[key].append(np.nan)

    return pandas.DataFrame(data)


def get_subject_mass(meta_file_path):

    with open(meta_file_path) as f:
        subject_mass = yaml.load(f)['subject']['mass']

    return subject_mass


def write_event_data_frame_to_disk(trial_number,
                                   event='Longitudinal Perturbation'):

    start = time.clock()

    trials_dir = trial_data_dir()
    file_paths = trial_file_paths(trials_dir, trial_number)

    tmp_dir = tmp_data_dir()
    event_data_path = os.path.join(tmp_dir, 'cleaned-data-' + trial_number +
                                   '-' + '-'.join(event.lower().split(' ')) +
                                   '.h5')

    try:
        f = open(event_data_path)
    except IOError:
        print('Cleaning the data.')
        dflow_data = motek.DFlowData(*file_paths)
        dflow_data.clean_data(interpolate_markers=True)
        event_data_frame = \
            dflow_data.extract_processed_data(event=event,
                                              index_col='TimeStamp')
        # TODO: Change the event name in the HDF5 file into one that is
        # natural naming compliant for PyTables.
        event_data_frame.to_hdf(event_data_path, event)
    else:
        print('Loading pre-cleaned data')
        f.close()
        event_data_frame = pandas.read_hdf(event_data_path, event)

    subject_mass = get_subject_mass(file_paths[2])

    print('{:1.2f} s'.format(time.clock() - start))

    return event_data_frame, subject_mass, event_data_path


def write_inverse_dynamics_to_disk(data_frame, subject_mass,
                                   event_data_path,
                                   inv_dyn_low_pass_cutoff=6.0):
    """Computes inverse kinematics and dynamics writes to disk."""

    # I use time.time() here because I thnk time.clock() doesn't count the
    # time spent in Octave on the inverse dynamics code.
    start = time.time()

    walking_data_path = event_data_path.replace('cleaned-data',
                                                'walking-data')

    try:
        f = open(walking_data_path)
    except IOError:
        print('Computing the inverse dynamics.')
        # Here I compute the joint angles, rates, and torques, which all are
        # low pass filtered.
        inv_dyn_labels = motek.markers_for_2D_inverse_dynamics()
        new_inv_dyn_labels = add_negative_columns(data_frame, 'Z',
                                                  inv_dyn_labels)
        walking_data = WalkingData(data_frame)

        args = new_inv_dyn_labels + [subject_mass, inv_dyn_low_pass_cutoff]

        walking_data.inverse_dynamics_2d(*args)

        walking_data.save(walking_data_path)
    else:
        print('Loading pre-computed inverse dynamics.')
        f.close()
        walking_data = WalkingData(walking_data_path)

    print('{:1.2f} s'.format(time.time() - start))

    return walking_data, walking_data_path


def section_signals_into_steps(walking_data, walking_data_path,
                               filter_frequency=15.0, threshold=30.0,
                               num_samples_lower_bound=53,
                               num_samples_upper_bound=132):
    """Computes inverse kinematics and dynamics and sections into steps."""

    def getem():
        print('Finding the ground reaction force landmarks.')
        start = time.clock()
        walking_data.grf_landmarks('FP2.ForY', 'FP1.ForY',
                                   filter_frequency=15.0, threshold=30.0)
        print('{:1.2f} s'.format(time.clock() - start))

        print('Spliting the data into steps.')
        start = time.clock()
        walking_data.split_at('right', num_samples=20,
                              belt_speed_column='RightBeltSpeed')
        print('{:1.2f} s'.format(time.clock() - start))

        walking_data.save(walking_data_path)

    try:
        f = open(walking_data_path)
    except IOError:
        getem()
    else:
        f.close()
        start = time.clock()
        walking_data = WalkingData(walking_data_path)
        if not hasattr(walking_data, 'steps'):
            getem()
        else:
            print('Loading pre-computed steps.')
            print(time.clock() - start)

    # Remove bad steps based on # samples in each step.
    valid = (walking_data.step_data['Number of Samples'] <
             num_samples_upper_bound)
    lower_values = walking_data.step_data[valid]

    valid = num_samples_lower_bound < lower_values['Number of Samples']
    mid_values = lower_values[valid]

    return walking_data.steps.iloc[mid_values.index], walking_data


def find_joint_isolated_controller(steps, event_data_path):
    # Controller identification.

    event = '-'.join(event_data_path[:-3].split('-')[-2:])
    gain_data_h5_path = event_data_path.replace('cleaned-data', 'gain-data')
    gain_data_npz_path = os.path.splitext(gain_data_h5_path)[0] + '.npz'

    print('Identifying the controller.')

    start = time.clock()

    sensors = ['Right.Ankle.PlantarFlexion.Angle',
               'Right.Ankle.PlantarFlexion.Rate',
               'Right.Knee.Flexion.Angle',
               'Right.Knee.Flexion.Rate',
               'Right.Hip.Flexion.Angle',
               'Right.Hip.Flexion.Rate',
               'Left.Ankle.PlantarFlexion.Angle',
               'Left.Ankle.PlantarFlexion.Rate',
               'Left.Knee.Flexion.Angle',
               'Left.Knee.Flexion.Rate',
               'Left.Hip.Flexion.Angle',
               'Left.Hip.Flexion.Rate']

    controls = ['Right.Ankle.PlantarFlexion.Moment',
                'Right.Knee.Flexion.Moment',
                'Right.Hip.Flexion.Moment',
                'Left.Ankle.PlantarFlexion.Moment',
                'Left.Knee.Flexion.Moment',
                'Left.Hip.Flexion.Moment']

    # Use the first 3/4 of the steps to compute the gains and validate on
    # the last 1/4. Most runs seem to be about 500 steps.
    num_steps = steps.shape[0]
    solver = SimpleControlSolver(steps.iloc[:num_steps * 3 / 4],
                                 sensors,
                                 controls,
                                 validation_data=steps.iloc[num_steps * 3 / 4:])

    # Limit to angles and rates from one joint can only affect the moment at
    # that joint.
    gain_omission_matrix = np.zeros((len(controls), len(sensors))).astype(bool)
    for i, row in enumerate(gain_omission_matrix):
        row[2 * i:2 * i + 2] = True

    try:
        f = open(gain_data_h5_path)
        f.close()
        f = open(gain_data_npz_path)
    except IOError:
        result = solver.solve(gain_omission_matrix=gain_omission_matrix)
        # first items are numpy arrays
        np.savez(gain_data_npz_path, *result[:-1])
        # the last item is a panel
        result[-1].to_hdf(gain_data_h5_path, event)
    else:
        f.close()
        with np.load(gain_data_npz_path) as npz:
            result = [npz['arr_0'],
                      npz['arr_1'],
                      npz['arr_2'],
                      npz['arr_3'],
                      npz['arr_4']]
        result.append(pandas.read_hdf(gain_data_h5_path, event))
        solver.gain_omission_matrix = gain_omission_matrix

    print('{:1.2f} s'.format(time.clock() - start))

    return sensors, controls, result, solver


def plot_joint_isolated_gains(sensor_labels, control_labels, gains,
                              gains_variance, axes=None, show_std=True,
                              linestyle='-'):

    print('Generating gain plot.')

    start = time.clock()

    if axes is None:
        fig, axes = plt.subplots(3, 2, sharex=True)
    else:
        fig = axes[0, 0].figure

    for i, (row, sign) in enumerate(zip(['Ankle', 'Knee', 'Hip'],
                                        ['PlantarFlexion', 'Flexion',
                                         'Flexion'])):
        for j, (col, unit) in enumerate(zip(['Angle', 'Rate'],
                                            ['Nm/rad', r'Nm $\cdot$ s/rad'])):
            for side, marker, color in zip(['Right', 'Left'],
                                           ['o', 'o'],
                                           ['Blue', 'Red']):

                row_label = '.'.join([side, row, sign + '.Moment'])
                col_label = '.'.join([side, row, sign, col])

                gain_row_idx = control_labels.index(row_label)
                gain_col_idx = sensor_labels.index(col_label)

                gains_per = gains[:, gain_row_idx, gain_col_idx]
                sigma = np.sqrt(gains_variance[:, gain_row_idx, gain_col_idx])

                percent_of_gait_cycle = np.linspace(0.0, 1.0, num=gains.shape[0])

                xlim = (percent_of_gait_cycle[0], percent_of_gait_cycle[-1])

                if side == 'Left':
                    # Shift that diggidty-dogg signal 50%
                    # This only works for an even number of samples.
                    if len(percent_of_gait_cycle) % 2 != 0:
                        raise StandardError("Doesn't work with odd samples.")

                    first = percent_of_gait_cycle[percent_of_gait_cycle < 0.5] + 0.5
                    second = percent_of_gait_cycle[percent_of_gait_cycle > 0.5] - 0.5
                    percent_of_gait_cycle = np.hstack((first, second))

                    # sort and sort gains/sigma same way
                    sort_idx = np.argsort(percent_of_gait_cycle)
                    percent_of_gait_cycle = percent_of_gait_cycle[sort_idx]
                    gains_per = gains_per[sort_idx]
                    sigma = sigma[sort_idx]

                if show_std:
                    axes[i, j].fill_between(percent_of_gait_cycle,
                                            gains_per - sigma,
                                            gains_per + sigma,
                                            alpha=0.5,
                                            color=color)

                axes[i, j].plot(percent_of_gait_cycle, gains_per,
                                marker='o',
                                ms=2,
                                color=color,
                                label=side,
                                linestyle=linestyle)

                #axes[i, j].set_title(' '.join(col_label.split('.')[1:]))
                axes[i, j].set_title(r"{}: {} $\rightarrow$ Moment".format(row, col))

                axes[i, j].set_ylabel(unit)

                if i == 2:
                    axes[i, j].set_xlabel(r'% of Gait Cycle')
                    axes[i, j].xaxis.set_major_formatter(_percent_formatter)
                    axes[i, j].set_xlim(xlim)

    leg = axes[0, 0].legend(('Right', 'Left'), loc='best', fancybox=True,
                            fontsize=8)
    leg.get_frame().set_alpha(0.75)

    print('{:1.2f} s'.format(time.clock() - start))

    plt.tight_layout()

    return fig, axes


def variance_accounted_for(estimated_panel, validation_panel, controls):
    """Returns a dictionary of R^2 values for each control."""

    estimated_walking = pandas.concat([df for k, df in
                                       estimated_panel.iteritems()],
                                      ignore_index=True)

    actual_walking = pandas.concat([df for k, df in
                                    validation_panel.iteritems()],
                                   ignore_index=True)

    vafs = {}

    for i, control in enumerate(controls):
        measured = actual_walking[control].values
        predicted = estimated_walking[control].values
        r_squared = coefficient_of_determination(measured, predicted)
        vafs[control] = r_squared

    return vafs


def plot_validation(estimated_controls, continuous, vafs):
    print('Generating validation plot.')
    start = time.clock()
    # get the first and last time of the estimated controls (10 steps)
    beg_first_step = estimated_controls.iloc[0]['Original Time'][0]
    end_last_step = estimated_controls.iloc[9]['Original Time'][-1]
    period = continuous[beg_first_step:end_last_step]

    # make plot for right and left legs
    fig, axes = plt.subplots(3, 2, sharex=True)

    moments = ['Ankle.PlantarFlexion.Moment',
               'Knee.PlantarFlexion.Moment',
               'Hip.PlantarFlexion.Moment']

    for j, side in enumerate(['Right', 'Left']):
        for i, moment in enumerate(moments):
            m = '.'.join([side, moment])
            axes[i, j].plot(period.index.values.astype(float),
                            period[m].values, color='black')

            est_x = []
            est_y = []
            for null, step in estimated_controls.iteritems():
                est_x.append(step['Original Time'].values)
                est_y.append(step[m].values)

            axes[i, j].plot(np.hstack(est_x), np.hstack(est_y), '.',
                            color='blue')

            axes[i, j].legend(('Measured',
                               'Estimated {:1.1%}'.format(vafs[m])), fontsize=8)

            if j == 0:
                axes[i, j].set_ylabel(moment.split('.')[0] + ' Torque [Nm]')

            if j == 1:
                axes[i, j].get_yaxis().set_ticks([])

    for i, m in enumerate(moments):
        adjacent = (period['Right.' + m].values, period['Left.' + m].values)
        axes[i, 0].set_ylim((np.max(np.hstack(adjacent)),
                             np.min(np.hstack(adjacent))))
        axes[i, 1].set_ylim((np.max(np.hstack(adjacent)),
                             np.min(np.hstack(adjacent))))

    axes[0, 0].set_xlim((beg_first_step, end_last_step))

    axes[0, 0].set_title('Right Leg')
    axes[0, 1].set_title('Left Leg')

    axes[-1, 0].set_xlabel('Time [s]')
    axes[-1, 1].set_xlabel('Time [s]')

    plt.tight_layout()

    print('{:1.2f} s'.format(time.clock() - start))

    return fig, axes


def mean_joint_isolated_gains(trial_numbers, sensors, controls, num_gains):

    data_dir = tmp_data_dir()

    all_gains = np.zeros((len(trial_numbers), num_gains, len(controls),
                          len(sensors)))

    for i, trial_number in enumerate(trial_numbers):
        file_name = 'gain-data-{}-longitudinal-perturbation.npz'.format(trial_number)
        gain_data_npz_path = os.path.join(data_dir, file_name)
        with np.load(gain_data_npz_path) as npz:
            # n, q, p
            all_gains[i] = npz['arr_0']
            # TODO : use proper uncertainties to compute errorbars
            #gain_var = npz['arr_3']

    # compute the mean and var
    mean_gains = all_gains.mean(axis=0)
    var_gains = all_gains.var(axis=0)

    return mean_gains, var_gains
